"""Risk ajanı: GLM tool-calling döngüsü + deterministik politika + kural tabanlı fallback."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import policy, rules
from .budget import Budget, BudgetExceeded, Usage
from .config import Settings
from .facts import Facts
from .glm_client import GLMBudgetError, GLMClient, GLMError
from .policy import LEVELS, level_index
from .prompts import FEEDBACK_NO_SUBMIT, SYSTEM_PROMPT, build_user_message
from .store import AssessmentStore
from .tools import TOOL_DEFS, ToolRunner, Upstreams, UpstreamError

log = logging.getLogger("risk-agent")


class LLMProtocolError(Exception):
    """Model beklenen sözleşmeye uymadı (submit_assessment gelmedi vb.)."""


class DetectionNotFound(Exception):
    pass


@dataclass
class LLMOutcome:
    risk_level: str
    confidence: float
    rationale: str
    evidence: list[dict[str, Any]]


def validate_submission(args: dict[str, Any]) -> tuple[LLMOutcome | None, list[str]]:
    """submit_assessment argümanlarını doğrular. evidence_breakdown BOŞ olamaz."""
    errors: list[str] = []
    level = str(args.get("risk_level", "")).strip().upper()
    if level not in LEVELS:
        errors.append(f"risk_level {list(LEVELS)} değerlerinden biri olmalı")
    try:
        conf = float(args.get("confidence"))
        if 1.0 < conf <= 100.0:
            conf /= 100.0
        if not 0.0 <= conf <= 1.0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("confidence 0-1 arasında sayı olmalı")
        conf = 0.0
    rationale = str(args.get("rationale", "")).strip()
    if not rationale:
        errors.append("rationale boş olamaz")
    evidence = policy.normalize_evidence(args.get("evidence_breakdown"))
    if not evidence:
        errors.append(
            "evidence_breakdown boş/geçersiz: en az bir madde gerekli "
            f"(source ∈ {list(policy.SOURCES)}, weight, summary)"
        )
    if errors:
        return None, errors
    return LLMOutcome(level, conf, rationale, evidence), []


def _try_parse_text(text: str | None) -> tuple[LLMOutcome | None, list[str]]:
    """Model düz metinle JSON döndürdüyse (submit_assessment çağırmadan) kurtarmayı dener."""
    if not text:
        return None, ["boş yanıt"]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, ["JSON bulunamadı"]
    try:
        return validate_submission(json.loads(text[start : end + 1]))
    except json.JSONDecodeError:
        return None, ["JSON çözümlenemedi"]


class RiskAgent:
    def __init__(
        self,
        settings: Settings,
        upstreams: Upstreams,
        glm: GLMClient | None,
        budget: Budget,
        store: AssessmentStore,
        persist: Any | None = None,
    ) -> None:
        self._s = settings
        self._up = upstreams
        self._glm = glm
        self._budget = budget
        self._store = store
        self._persist = persist
        self._sem = asyncio.Semaphore(settings.max_concurrent)
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ giriş noktası
    async def assess(self, zone_id: str, detection_id: str, force: bool = False, base_location: dict[str, float] | None = None) -> dict[str, Any]:
        """Aynı (zone, detection) için eşzamanlı çağrılar tek değerlendirmeye indirgenir; sonuç önbelleğe alınır."""
        key = f"{zone_id.upper()}|{detection_id}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                if not force and (cached := self._store.find(zone_id, detection_id)) is not None:
                    return {**cached, "cached": True}
                async with self._sem:
                    result = await self._assess(zone_id, detection_id, base_location)
                self._store.put(result)
                if self._persist is not None:
                    await asyncio.to_thread(self._persist.save_assessment, result)
                return result
        finally:
            if not lock.locked():
                self._locks.pop(key, None)

    # ------------------------------------------------------------------ asıl akış
    async def _assess(self, zone_id: str, detection_id: str, base_location: dict[str, float] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        t0 = time.perf_counter()
        try:
            detection = await self._up.get_detection(detection_id)
        except UpstreamError as exc:
            if exc.status == 404:
                raise DetectionNotFound(detection_id) from exc
            raise
        s = self._s
        detection = dict(detection)
        all_ids = list(detection.get("vehicle_ids") or [])
        detection["vehicle_ids"] = all_ids[: s.max_vehicles]
        dropped = all_ids[s.max_vehicles :]
        dets = [d for d in detection.get("detections", []) if d.get("lat") is not None]
        event_point = {"lat": sum(d["lat"] for d in dets) / len(dets), "lon": sum(d["lon"] for d in dets) / len(dets)} if dets else None
        # Üs: çağıranın (gateway) verdiği; yoksa ortam varsayılanı (eski/demo)
        base = {
            "lat": float(base_location["lat"]), "lon": float(base_location["lon"]),
            "radius_m": float(base_location.get("radius_m") or s.base_radius_m),
        } if base_location else {"lat": s.base_lat, "lon": s.base_lon, "radius_m": s.base_radius_m}
        facts = Facts(
            zone_id=zone_id.upper(),
            detection=detection,
            base=base,
            reference_time=detection.get("reference_time"),
            event_point=event_point,
            dropped_vehicle_ids=dropped,
        )
        runner = ToolRunner(s, self._up, facts)
        runner.add_log(
            "get_detection_context",
            {"detection_id": detection_id},
            "ok",
            (time.perf_counter() - t0) * 1000,
            f"{len(detection.get('detections', []))} tespit, izli araçlar: {', '.join(facts.vehicle_ids) or '—'}, değerlendirme anı: {facts.reference_time or '—'}",
            {"vehicle_ids": facts.vehicle_ids, "drone_id": facts.drone_id, "reference_time": facts.reference_time, "capture_time": detection.get("capture_time")},
            origin="system",
        )

        usage = Usage()
        mode, fallback_reason = "rule-based", None
        outcome: LLMOutcome | None = None
        if not facts.vehicle_ids:
            fallback_reason = "tespitte izle eşleşen araç yok"
        elif self._glm is None:
            fallback_reason = f"LLM anahtarı tanımlı değil (sağlayıcı: {s.llm_provider})"
        else:
            try:
                await self._budget.reserve_assessment()
            except BudgetExceeded as exc:
                if s.on_budget_exceeded == "reject":
                    raise
                fallback_reason = f"bütçe/kota: {exc}"
            else:
                try:
                    outcome = await self._run_llm(facts, runner, usage)
                    mode = "llm"
                except BudgetExceeded as exc:  # tur ortasında sınır aşımı: her zaman fallback
                    fallback_reason = f"bütçe/kota: {exc}"
                except GLMBudgetError as exc:  # gateway: 400 "Budget has been exceeded" — sonraki değerlendirmeler LLM'i denemez
                    self._budget.mark_exhausted()
                    log.error("GLM bütçesi bitti: %s", exc)
                    fallback_reason = f"bütçe/kota: {exc}"
                except (GLMError, LLMProtocolError) as exc:
                    log.warning("LLM başarısız, kural motoruna düşülüyor: %s", exc)
                    fallback_reason = f"LLM: {exc}"

        await self._ensure_data(facts, runner, full=outcome is None)
        own = policy.own_data_level(facts)

        adjustments: list[str] = []
        if outcome is not None:
            final_idx, notes = policy.enforce(level_index(outcome.risk_level), own)
            adjustments.extend(notes)
            rationale = outcome.rationale
            if notes:
                rationale += " (Politika düzeltmesi: " + " ".join(notes) + ")"
            evidence = outcome.evidence
            confidence = min(outcome.confidence, 0.4) if own.insufficient else outcome.confidence
        else:
            final_idx = own.idx
            evidence = rules.build_evidence(facts, own)
            rationale = rules.build_rationale(facts, own, final_idx)
            confidence = rules.build_confidence(facts, own)
        if own.convoy_bumped:
            adjustments.insert(0, next(r for r in own.reasons if "CONVOY" in r))
        gaps = facts.data_gaps()
        own_reasons = list(own.reasons)
        if gaps:
            own_reasons.append("Veri boşluğu: " + gaps["note"])
            if outcome is not None:  # LLM gerekçesi bunu anmasa da boşluk her zaman görünür
                rationale += " (Veri boşluğu: " + gaps["note"] + ")"

        pattern = None
        if facts.pattern:
            pattern = {
                "pattern": facts.pattern.get("pattern"),
                "confidence": facts.pattern.get("confidence"),
                "matched": facts.matched_patterns(),
                "involved_vehicles": facts.pattern.get("involved_vehicles"),
            }
        result = {
            "assessment_id": "asm-" + uuid.uuid4().hex[:12],
            "zone_id": facts.zone_id,
            "detection_id": detection_id,
            "reference_time": facts.reference_time,
            "capture_time": detection.get("capture_time"),
            "base": facts.base,
            "drone_id": facts.drone_id,
            "vehicle_ids": facts.vehicle_ids,
            "risk_level": LEVELS[final_idx],
            "confidence": round(float(confidence), 2),
            "rationale": rationale,
            "evidence_breakdown": evidence,
            "tool_calls_log": runner.log,
            "mode": mode,
            "model": s.glm_model if mode == "llm" else None,
            "fallback_reason": fallback_reason,
            "own_data_level": LEVELS[own.idx],
            "own_data_reasons": own_reasons,
            "data_gaps": gaps,
            "policy_adjustments": adjustments,
            "report_verification": facts.report_verification,
            "pattern": pattern,
            "usage": usage.as_dict(),
            "budget": self._budget.status(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "cached": False,
        }
        log.info(
            "assess zone=%s det=%s -> %s (%.2f) mode=%s tools=%d tokens=%d",
            facts.zone_id, detection_id, result["risk_level"], result["confidence"], mode, len(runner.log), usage.total_tokens,
        )
        return result

    async def _ensure_data(self, facts: Facts, runner: ToolRunner, full: bool) -> None:
        """Politikanın ihtiyaç duyduğu zorunlu verileri (LLM çağırmadıysa) kendisi toplar. full=True: tüm araçlar."""
        for vid in facts.vehicle_ids:
            if vid not in facts.movement:
                await runner.run("get_movement_analysis", {"vehicle_id": vid}, origin="auto")
        if facts.vehicle_ids and facts.pattern is None:
            await runner.run("get_pattern_classification", {"vehicle_ids": facts.vehicle_ids, "zone_id": facts.zone_id}, origin="auto")
        if not full:
            return
        if facts.drone is None and facts.drone_id:
            await runner.run("get_drone_context", {"drone_id": facts.drone_id}, origin="auto")
        if facts.intel is None:
            await runner.run("get_intel", {"zone_id": facts.zone_id}, origin="auto")
        if facts.reports is None:
            await runner.run("get_reports", {"zone_id": facts.zone_id}, origin="auto")

    # ------------------------------------------------------------------ LLM döngüsü
    async def _run_llm(self, facts: Facts, runner: ToolRunner, usage: Usage) -> LLMOutcome:
        assert self._glm is not None
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(facts)},
        ]
        nudges = 0
        for _ in range(self._s.max_rounds):
            await self._budget.before_call(usage.total_tokens)
            resp = await self._glm.chat(messages, TOOL_DEFS)
            u = resp.get("usage") or {}
            usage.add(u)
            await self._budget.record(int(u.get("total_tokens", 0) or 0))

            msg = resp["choices"][0].get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            assistant: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            messages.append(assistant)

            if not tool_calls:
                parsed, _errs = _try_parse_text(msg.get("content"))
                if parsed is not None:
                    runner.add_log("submit_assessment", {"via": "plain-json"}, "ok", 0.0, f"risk={parsed.risk_level} güven={parsed.confidence:.2f}", {"evidence_items": len(parsed.evidence)}, "llm")
                    return parsed
                nudges += 1
                if nudges > 2:
                    raise LLMProtocolError("model submit_assessment çağırmadı")
                messages.append({"role": "user", "content": FEEDBACK_NO_SUBMIT})
                continue

            outcome: LLMOutcome | None = None
            for i, tc in enumerate(tool_calls):
                fn = tc.get("function") or {}
                name = str(fn.get("name", ""))
                tc_id = tc.get("id") or f"call_{i}"
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("argümanlar nesne olmalı")
                    args_error = None
                except (json.JSONDecodeError, ValueError) as exc:
                    args, args_error = {}, f"arguments geçerli JSON nesnesi değil: {exc}"

                if args_error:
                    result: dict[str, Any] = {"error": args_error}
                    runner.add_log(name or "?", {}, "error", 0.0, f"HATA: {args_error}", result, "llm")
                elif name == "submit_assessment":
                    parsed, errors = validate_submission(args)
                    if parsed is not None:
                        outcome = parsed
                        result = {"ok": True}
                        runner.add_log(name, args, "ok", 0.0, f"risk={parsed.risk_level} güven={parsed.confidence:.2f}", {"ok": True}, "llm")
                    else:
                        result = {"error": "; ".join(errors)}
                        runner.add_log(name, args, "error", 0.0, "HATA: " + "; ".join(errors), result, "llm")
                else:
                    result = await runner.run(name, args, origin="llm")
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": json.dumps(result, ensure_ascii=False)})
            if outcome is not None:
                return outcome
        raise LLMProtocolError(f"maksimum tur ({self._s.max_rounds}) aşıldı")
