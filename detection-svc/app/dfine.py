"""D-FINE dedektörü (MODEL_BACKEND=dfine).

Beklenen dosya: resmî D-FINE reposunun (https://github.com/Peterande/D-FINE) eğitim checkpoint'i — `model` ve
(varsa) `ema` state_dict'leri. Model, o reponun `src` paketi + yapılandırma dosyasıyla kurulur (DFINE_REPO/DFINE_CONFIG);
`torch` ve repo yalnızca bu backend seçilince, tembel import edilir. Çıkarım yolu, checkpoint'i üreten eğitim
not defteriyle aynıdır: Resize((S, S)) + ToTensor (mean/std YOK), EMA ağırlıkları, postprocessor orijinal (w, h) verilerek
kutuları doğrudan orijinal piksel koordinatında döndürür.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
from typing import Sequence

from PIL import Image

from .config import Settings
from .detector import Det, ModelLoadError

log = logging.getLogger("detection-svc.dfine")

# D-FINE çıpa sayısı = (S/8)² + (S/16)² + (S/32)²  →  S = sqrt(N / (1/64 + 1/256 + 1/1024))
_ANCHOR_FACTOR = 1 / 64 + 1 / 256 + 1 / 1024


def size_from_anchor_count(n_anchors: int) -> int:
    """Checkpoint'in `decoder.anchors` uzunluğundan eğitim girdi boyutunu (kare kenar) türetir."""
    s = math.sqrt(n_anchors / _ANCHOR_FACTOR)
    if abs(s - round(s)) > 1e-6 or round(s) % 32:
        raise ModelLoadError(f"decoder.anchors={n_anchors} bilinen bir D-FINE girdi boyutuna karşılık gelmiyor")
    return int(round(s))


def to_detections(
    labels: Sequence[int],
    boxes: Sequence[Sequence[float]],
    scores: Sequence[float],
    *,
    class_names: Sequence[str],
    conf_threshold: float,
    wanted: set[str],
    width: int,
    height: int,
    max_detections: int,
) -> list[Det]:
    """Postprocessor çıktısını (orijinal piksel, xyxy) süzülmüş `Det` listesine çevirir: skor eşiği, sınıf süzgeci,
    görüntü sınırına kırpma, dejenere kutuları atma, en yüksek skordan başlayarak üst sınır."""
    order = sorted(range(len(scores)), key=lambda i: -float(scores[i]))
    out: list[Det] = []
    for i in order:
        conf = float(scores[i])
        if conf < conf_threshold:
            break  # skora göre sıralı
        idx = int(labels[i])
        if not 0 <= idx < len(class_names):
            continue
        name = class_names[idx]
        if wanted and name.lower() not in wanted:
            continue
        x1, y1, x2, y2 = (float(v) for v in boxes[i])
        x1, x2 = max(0.0, min(x1, width)), max(0.0, min(x2, width))
        y1, y2 = max(0.0, min(y1, height)), max(0.0, min(y2, height))
        if x2 - x1 < 1.0 or y2 - y1 < 1.0:
            continue
        out.append(Det(name, round(conf, 4), round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)))
        if len(out) >= max_detections:
            break
    return out


class DFineDetector:
    mode = "model"
    backend = "dfine"
    fallback_reason: str | None = None

    def __init__(self, settings: Settings) -> None:
        if not os.path.isfile(settings.model_path):
            raise ModelLoadError(f"Model dosyası yok: {settings.model_path}")
        cfg_path = os.path.join(settings.dfine_repo, settings.dfine_config)
        if not os.path.isfile(cfg_path):
            raise ModelLoadError(
                f"D-FINE yapılandırması yok: {cfg_path} (DFINE_REPO={settings.dfine_repo!r} resmî repo checkout'unu göstermeli)"
            )
        try:
            import torch  # noqa: PLC0415
            import torch.nn as nn  # noqa: PLC0415
            import torchvision.transforms as T  # noqa: PLC0415
        except ImportError as exc:
            raise ModelLoadError(f"torch/torchvision kurulu değil ({exc}); WITH_MODEL=true imajı ya da requirements-model.txt gerekir") from exc

        if settings.dfine_repo not in sys.path:
            sys.path.insert(0, settings.dfine_repo)
        try:
            from src.core import YAMLConfig  # noqa: PLC0415  (resmî repo)
        except ImportError as exc:
            raise ModelLoadError(f"D-FINE reposu içe aktarılamadı ({exc}); repo bağımlılıkları (faster-coco-eval, calflops, transformers…) eksik olabilir") from exc

        # weights_only=True: yalnızca tensör/sözlük yükler, pickle üzerinden kod çalıştırmaz.
        ckpt = torch.load(settings.model_path, map_location="cpu", weights_only=True)
        if not isinstance(ckpt, dict) or "model" not in ckpt:
            raise ModelLoadError("Checkpoint D-FINE biçiminde değil ('model' anahtarı yok)")
        use_ema = settings.dfine_use_ema and isinstance(ckpt.get("ema"), dict) and "module" in ckpt["ema"]
        state = ckpt["ema"]["module"] if use_ema else ckpt["model"]

        anchors = state.get("decoder.anchors")
        if anchors is None:
            raise ModelLoadError("Checkpoint'te decoder.anchors yok; D-FINE modeli değil")
        self._size = size_from_anchor_count(int(anchors.shape[1]))
        n_cls = int(state["decoder.enc_score_head.weight"].shape[0])
        if n_cls != len(settings.class_names):
            raise ModelLoadError(f"Checkpoint {n_cls} sınıflı, CLASS_NAMES={settings.class_names} ({len(settings.class_names)} sınıf)")
        if settings.img_size != self._size:
            log.warning("IMG_SIZE=%s yok sayıldı: checkpoint çıpaları %sx%s girdi için eğitilmiş", settings.img_size, self._size, self._size)

        cfg = YAMLConfig(
            cfg_path,
            num_classes=n_cls,
            remap_mscoco_category=False,
            eval_spatial_size=[self._size, self._size],
            num_top_queries=300,
            DFINEPostProcessor={"num_top_queries": 300},
        )
        cfg.yaml_cfg["HGNetv2"]["pretrained"] = False  # tüm ağırlıklar checkpoint'ten gelir
        cfg.model.load_state_dict(state)  # strict: uyumsuzluk burada patlar

        class _Deploy(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.model = cfg.model.deploy()
                self.postprocessor = cfg.postprocessor.deploy()

            def forward(self, images, orig_sizes):  # type: ignore[no-untyped-def]
                return self.postprocessor(self.model(images), orig_sizes)

        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = _Deploy().to(self._device).eval()
        self._tf = T.Compose([T.Resize((self._size, self._size)), T.ToTensor()])
        self._lock = threading.Lock()  # tek model örneği; çıkarım CPU/GPU-bağlı, eşzamanlı çağrıları sıraya diz
        self._s = settings
        self._wanted = {c.lower() for c in settings.vehicle_classes}
        log.info(
            "D-FINE yüklendi: %s (%s ağırlıkları, last_epoch=%s, girdi %sx%s, sınıflar %s, cihaz %s)",
            settings.model_path, "EMA" if use_ema else "model", ckpt.get("last_epoch"), self._size, self._size, settings.class_names, self._device,
        )

    def detect(self, img: Image.Image | None, drone_id: str | None, seed_key: str, image_id: str | None = None) -> tuple[list[Det], int, int]:
        if img is None:
            raise ValueError("Model modunda görüntü zorunludur")
        rgb = img.convert("RGB")
        w, h = rgb.size
        torch = self._torch
        with self._lock, torch.inference_mode():
            x = self._tf(rgb).unsqueeze(0).to(self._device)
            sizes = torch.tensor([[w, h]], device=self._device)
            labels, boxes, scores = self._model(x, sizes)
        dets = to_detections(
            labels[0].cpu().tolist(),
            boxes[0].cpu().tolist(),
            scores[0].cpu().tolist(),
            class_names=self._s.class_names,
            conf_threshold=self._s.conf_threshold,
            wanted=self._wanted,
            width=w,
            height=h,
            max_detections=self._s.max_detections,
        )
        return dets, w, h
