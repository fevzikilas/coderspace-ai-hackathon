#!/usr/bin/env python3
"""
Production Multi-Scale & TTA Inference Engine for Aerial Vehicle Detection
==========================================================================
Supports both D-FINE and YOLO architectures with:
- Multi-scale inference (1280, 1536, 1792)
- Optional Horizontal Flip Test-Time Augmentation (TTA) with consensus fusion
- Genuinely Class-Aware NMS (boxes of different classes never suppress each other)
- Coordinate restoration into original image dimensions
- Boundary clamping, integer rounding, and confidence-sorted formatting
- Built-in strict submission validation against sample_submission.csv

Competition Taxonomy:
  0: car, 1: van, 2: bus, 3: truck

Usage Examples:
  # D-FINE Multi-Scale (1280 + 1536 + 1792):
  python inference.py --model-type dfine --weights /path/to/bestofbest-dfine.pt \\
      --test-dir data/test/images --sample-sub data/sample_submission.csv \\
      --output submission_dfine.csv --scales 1280 1536 1792 --nms-iou 0.70

  # YOLO Multi-Scale (1280 + 1536 + 1792):
  python inference.py --model-type yolo --weights runs/train/yolo26l/weights/best.pt \\
      --test-dir data/test/images --sample-sub data/sample_submission.csv \\
      --output submission_yolo.csv --scales 1280 1536 1792 --nms-iou 0.70

  # YOLO TTA Consensus Fusion (1536 + 1792 + 1792-HFlip):
  python inference.py --model-type yolo --weights runs/train/yolo26l/weights/best.pt \\
      --test-dir data/test/images --sample-sub data/sample_submission.csv \\
      --output submission_yolo_consensus.csv --scales 1536 1792 --use-hflip --nms-iou 0.60
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np
import pandas as pd
import torch
from PIL import Image

# Setup search paths for local repository imports
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "dfine_repo"))
sys.path.append(str(ROOT_DIR / "scripts"))

COMP_CLASS_NAMES = {0: "car", 1: "van", 2: "bus", 3: "truck"}
LABEL_TO_ID = {v: k for k, v in COMP_CLASS_NAMES.items()}
DFINE_RAW_TO_COMP = {0: 0, 1: 1, 2: 3, 3: 2}  # D-FINE native raw: 2=truck, 3=bus


# ==============================================================================
# 1. Coordinate Formatting & Submission Validation
# ==============================================================================
def format_prediction_string(
    boxes: Sequence[Tuple[str, float, float, float, float, float]],
    image_size: Tuple[int, int],
    min_area: float = 0.0,
) -> str:
    """Format bounding boxes into competition PredictionString: <label> <conf> <x> <y> <w> <h>"""
    if not boxes:
        return "none"

    width, height = int(image_size[0]), int(image_size[1])
    sorted_boxes = sorted(boxes, key=lambda b: float(b[1]), reverse=True)

    parts: list[str] = []
    for label, conf, raw_x1, raw_y1, raw_x2, raw_y2 in sorted_boxes:
        conf_val = float(conf)
        conf_text = f"{conf_val:.6f}"

        # 1. Clamp endpoints to image dimensions
        x1 = int(round(np.clip(float(raw_x1), 0, width)))
        y1 = int(round(np.clip(float(raw_y1), 0, height)))
        x2 = int(round(np.clip(float(raw_x2), 0, width)))
        y2 = int(round(np.clip(float(raw_y2), 0, height)))

        w = x2 - x1
        h = y2 - y1

        # Skip zero or negative area boxes
        if w <= 0 or h <= 0:
            continue
        if min_area > 0.0 and (w * h) < min_area:
            continue

        parts.append(f"{label} {conf_text} {x1} {y1} {w} {h}")

    return " ".join(parts) if parts else "none"


def validate_submission_file(
    df_sub: pd.DataFrame,
    df_sample: pd.DataFrame,
    test_image_dir: Path,
) -> Tuple[bool, int, int]:
    """Strictly validate submission against competition schema rules."""
    print("Running strict submission validation...")
    if list(df_sub.columns) != ["image_id", "PredictionString"]:
        raise ValueError(f"Invalid columns: {list(df_sub.columns)}, expected ['image_id', 'PredictionString']")

    if len(df_sub) != len(df_sample):
        raise ValueError(f"Row count mismatch: {len(df_sub)} vs sample {len(df_sample)}")

    if list(df_sub["image_id"]) != list(df_sample["image_id"]):
        raise ValueError("Image ID sequence or ordering does not exactly match sample_submission.csv")

    total_boxes = 0
    active_images = 0
    valid_classes = set(COMP_CLASS_NAMES.values())

    for _, row in df_sub.iterrows():
        img_id = str(row["image_id"])
        pred_str = str(row["PredictionString"]).strip()

        img_file = test_image_dir / f"{img_id}.jpg"
        if not img_file.exists():
            raise FileNotFoundError(f"Referenced test image does not exist: {img_file}")

        if pred_str == "none":
            continue

        active_images += 1
        tokens = pred_str.split()
        if len(tokens) % 6 != 0:
            raise ValueError(f"Malformed token count ({len(tokens)}) for {img_id}")

        with Image.open(img_file) as im:
            img_w, img_h = im.size

        last_conf = float("inf")
        for i in range(0, len(tokens), 6):
            lbl = tokens[i]
            conf = float(tokens[i + 1])
            x = int(tokens[i + 2])
            y = int(tokens[i + 3])
            w = int(tokens[i + 4])
            h = int(tokens[i + 5])

            if lbl not in valid_classes:
                raise ValueError(f"Unknown class '{lbl}' in {img_id}")
            if not (0.0 <= conf <= 1.0) or math.isnan(conf):
                raise ValueError(f"Invalid confidence '{conf}' in {img_id}")
            if conf > last_conf:
                raise ValueError(f"Predictions not sorted by confidence descending in {img_id}")
            last_conf = conf

            if x < 0 or y < 0 or w <= 0 or h <= 0:
                raise ValueError(f"Invalid box dimensions [{x}, {y}, {w}, {h}] in {img_id}")
            if (x + w) > img_w or (y + h) > img_h:
                raise ValueError(f"Box [{x}, {y}, {w}, {h}] exceeds image bounds ({img_w}x{img_h}) in {img_id}")

            total_boxes += 1

    print(f"  Validation Result: SUCCESS (is_valid=True)")
    print(f"  Total Validated Boxes: {total_boxes:,d}")
    print(f"  Images with Detections: {active_images} / {len(df_sub)} ({active_images / len(df_sub) * 100:.1f}%)")
    return True, total_boxes, active_images


# ==============================================================================
# 2. Class-Aware Non-Maximum Suppression (NMS)
# ==============================================================================
def class_aware_nms(
    rows: Sequence[dict[str, object]],
    iou_threshold: float = 0.70,
    confidence_floor: float = 0.001,
    max_det: int = 1000,
    min_area: float = 0.0,
) -> list[dict[str, object]]:
    """
    Execute class-aware NMS using coordinate offsets.
    Different classes are offset by 7680px so they never suppress each other.
    """
    from torchvision.ops import nms

    # 1. Filter by confidence and area
    filtered = []
    for r in rows:
        conf = float(r["confidence"])
        if conf < confidence_floor:
            continue
        w = float(r["x2"]) - float(r["x1"])
        h = float(r["y2"]) - float(r["y1"])
        if w * h < min_area:
            continue
        filtered.append(dict(r))

    # 2. Group by image_id
    by_image: dict[str, list[dict[str, object]]] = defaultdict(list)
    for r in filtered:
        by_image[str(r["image_id"])].append(r)

    output: list[dict[str, object]] = []
    for img_id in sorted(by_image):
        img_rows = by_image[img_id]
        ordered = sorted(img_rows, key=lambda row: -float(row["confidence"]))[:30000]

        boxes = torch.tensor([[float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"])] for r in ordered], dtype=torch.float32)
        scores = torch.tensor([float(r["confidence"]) for r in ordered], dtype=torch.float32)

        # Apply class offsets to isolate classes in coordinate space
        offsets = torch.tensor([int(r["class_id"]) * 7680 for r in ordered], dtype=torch.float32)
        offset_boxes = boxes + offsets[:, None]

        keep = nms(offset_boxes, scores, iou_threshold)[:max_det].tolist()
        output.extend(ordered[idx] for idx in keep)

    return output


# ==============================================================================
# 3. Geometric Cross-View / Cross-Scale Matching & Consensus
# ==============================================================================
def compute_pairwise_matches(
    primary: Sequence[dict[str, object]],
    secondary: Sequence[dict[str, object]],
    iou_threshold: float = 0.70,
) -> list[tuple[int, int, float]]:
    """Match bounding boxes between two prediction sets for the same images."""
    left_by_img: dict[str, list[int]] = defaultdict(list)
    right_by_img: dict[str, list[int]] = defaultdict(list)

    for i, r in enumerate(primary):
        left_by_img[str(r["image_id"])].append(i)
    for j, r in enumerate(secondary):
        right_by_img[str(r["image_id"])].append(j)

    matches: list[tuple[int, int, float]] = []
    for img_id in sorted(left_by_img.keys() & right_by_img.keys()):
        l_indices, r_indices = left_by_img[img_id], right_by_img[img_id]
        l_boxes = np.array([[float(primary[i][k]) for k in ("x1", "y1", "x2", "y2")] for i in l_indices], dtype=np.float32)
        r_boxes = np.array([[float(secondary[j][k]) for k in ("x1", "y1", "x2", "y2")] for j in r_indices], dtype=np.float32)

        tl = np.maximum(l_boxes[:, None, :2], r_boxes[None, :, :2])
        br = np.minimum(l_boxes[:, None, 2:], r_boxes[None, :, 2:])
        inter_dims = np.maximum(0, br - tl)
        intersection = inter_dims[..., 0] * inter_dims[..., 1]

        l_area = (l_boxes[:, 2] - l_boxes[:, 0]) * (l_boxes[:, 3] - l_boxes[:, 1])
        r_area = (r_boxes[:, 2] - r_boxes[:, 0]) * (r_boxes[:, 3] - r_boxes[:, 1])
        union = l_area[:, None] + r_area[None, :] - intersection

        overlaps = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
        locations = np.argwhere(overlaps >= iou_threshold)
        candidates = sorted(
            ((float(overlaps[i, j]), l_indices[int(i)], r_indices[int(j)]) for i, j in locations),
            reverse=True,
        )

        used_l: set[int] = set()
        used_r: set[int] = set()
        for ov, l_idx, r_idx in candidates:
            if l_idx in used_l or r_idx in used_r:
                continue
            used_l.add(l_idx)
            used_r.add(r_idx)
            matches.append((l_idx, r_idx, ov))

    return sorted(matches)


def build_consensus_candidates(
    primary: Sequence[dict[str, object]],
    secondary: Sequence[dict[str, object]],
    matches: Sequence[tuple[int, int, float]],
    agreement_value: float = 0.5,
) -> list[dict[str, object]]:
    """Modulate candidate confidence scores based on cross-view consensus."""
    used_l = {l: (r, ov) for l, r, ov in matches}
    used_r = {r: (l, ov) for l, r, ov in matches}
    output: list[dict[str, object]] = []

    for idx, row in enumerate(primary):
        r = dict(row)
        if idx in used_l:
            r_idx, ov = used_l[idx]
            other = secondary[r_idx]
            s1, s2 = float(r["confidence"]), float(other["confidence"])
            if int(r["class_id"]) == int(other["class_id"]):
                r["confidence"] = max(s1, s2) + agreement_value * min(s1, s2)
        output.append(r)

    for idx, row in enumerate(secondary):
        r = dict(row)
        if idx in used_r:
            l_idx, ov = used_r[idx]
            other = primary[l_idx]
            s1, s2 = float(other["confidence"]), float(r["confidence"])
            if int(r["class_id"]) == int(other["class_id"]):
                r["confidence"] = max(s1, s2) + agreement_value * min(s1, s2)
        output.append(r)

    return output


# ==============================================================================
# 4. Model Inference Engines
# ==============================================================================
def run_dfine_inference_scale(
    weights_path: Path,
    image_paths: Sequence[Path],
    imgsz: int,
    batch_size: int = 4,
    device_id: int = 0,
) -> list[dict[str, object]]:
    """Run D-FINE inference on image paths at a specific image scale."""
    from src.core import YAMLConfig
    import torchvision.transforms as T

    device = torch.device(f"cuda:{device_id}")
    print(f"[D-FINE] Initializing imgsz={imgsz} on {device}...", flush=True)

    cfg_path = ROOT_DIR / "dfine_repo/configs/dfine/dfine_hgnetv2_l_coco.yml"
    cfg = YAMLConfig(str(cfg_path))
    cfg.yaml_cfg["eval_spatial_size"] = [imgsz, imgsz]
    cfg.yaml_cfg["num_classes"] = 4

    ckpt = torch.load(weights_path, map_location="cpu")
    state = dict(ckpt["ema"]["module"] if "ema" in ckpt and "module" in ckpt["ema"] else ckpt["model"])
    if imgsz != 1280:
        state.pop("decoder.anchors", None)
        state.pop("decoder.valid_mask", None)

    cfg.model.load_state_dict(state, strict=(imgsz == 1280))
    model = cfg.model.deploy().to(device)
    postprocessor = cfg.postprocessor.deploy().to(device)

    transforms = T.Compose([
        T.Resize((imgsz, imgsz)),
        T.ToTensor(),
    ])

    results: list[dict[str, object]] = []
    t0 = time.time()

    for i in range(0, len(image_paths), batch_size):
        batch_paths = image_paths[i : i + batch_size]
        batch_tensors, batch_orig_sizes, batch_metadata = [], [], []

        for p in batch_paths:
            with Image.open(p) as im_pil:
                im_pil = im_pil.convert("RGB")
                w, h = im_pil.size
                im_tensor = transforms(im_pil)
            batch_tensors.append(im_tensor)
            batch_orig_sizes.append([w, h])
            batch_metadata.append((p.stem, w, h))

        images_tensor = torch.stack(batch_tensors).to(device)
        orig_sizes_tensor = torch.tensor(batch_orig_sizes, device=device)

        with torch.no_grad():
            outputs = model(images_tensor)
            labels, boxes, scores = postprocessor(outputs, orig_sizes_tensor)

        for b_idx, (img_id, w, h) in enumerate(batch_metadata):
            b_labels = labels[b_idx].cpu().numpy()
            b_boxes = boxes[b_idx].cpu().numpy()
            b_scores = scores[b_idx].cpu().numpy()

            for lab, box, sco in zip(b_labels, b_boxes, b_scores):
                if sco < 0.001:
                    continue
                results.append({
                    "image_id": img_id,
                    "class_id": int(lab),  # Keep raw model class for class-aware NMS
                    "confidence": float(sco),
                    "x1": float(box[0]),
                    "y1": float(box[1]),
                    "x2": float(box[2]),
                    "y2": float(box[3]),
                    "image_width": int(w),
                    "image_height": int(h),
                })

        if (i // batch_size + 1) % 50 == 0 or (i + batch_size) >= len(image_paths):
            elapsed = time.time() - t0
            done = min(i + batch_size, len(image_paths))
            print(f"[{imgsz}px] {done}/{len(image_paths)} images processed ({elapsed:.1f}s, {elapsed/done*1000:.1f} ms/img)", flush=True)

    del model, postprocessor
    torch.cuda.empty_cache()
    return results


def run_yolo_inference_scale(
    weights_path: Path,
    image_paths: Sequence[Path],
    imgsz: int,
    batch_size: int = 8,
    view: str = "original",
    device_id: int = 0,
) -> list[dict[str, object]]:
    """Run YOLO inference on image paths at a specific image scale."""
    from ultralytics import YOLO

    print(f"[YOLO] Initializing imgsz={imgsz}, view={view} on cuda:{device_id}...", flush=True)
    model = YOLO(str(weights_path))

    results: list[dict[str, object]] = []
    t0 = time.time()

    for i in range(0, len(image_paths), batch_size):
        batch_paths = image_paths[i : i + batch_size]

        if view == "hflip":
            # Pass horizontally flipped numpy arrays
            batch_inputs = []
            for p in batch_paths:
                with Image.open(p) as opened:
                    rgb = np.asarray(opened.convert("RGB"))
                batch_inputs.append(np.ascontiguousarray(rgb[:, ::-1, ::-1]))
        else:
            batch_inputs = [str(p) for p in batch_paths]

        with torch.inference_mode():
            batch_results = model.predict(
                source=batch_inputs,
                imgsz=imgsz,
                conf=0.001,
                iou=0.70,
                max_det=1000,
                device=device_id,
                quantize=16,
                augment=False,
                verbose=False,
                stream=False,
            )

        for p, res in zip(batch_paths, batch_results):
            img_id = p.stem
            boxes = res.boxes
            if boxes is None or len(boxes) == 0:
                continue

            orig_h, orig_w = res.orig_shape[0], res.orig_shape[1]
            xyxy = boxes.xyxy.detach().cpu().numpy()
            clss = boxes.cls.detach().cpu().numpy().astype(int)
            confs = boxes.conf.detach().cpu().numpy()

            for box, c_id, score in zip(xyxy, clss, confs):
                if score < 0.001:
                    continue
                x1, y1, x2, y2 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
                if view == "hflip":
                    # Unflip coordinates back to original frame
                    x1_unflip = float(orig_w) - x2
                    x2_unflip = float(orig_w) - x1
                    x1, x2 = x1_unflip, x2_unflip

                results.append({
                    "image_id": img_id,
                    "class_id": int(c_id),
                    "confidence": float(score),
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "image_width": int(orig_w),
                    "image_height": int(orig_h),
                })

        if (i // batch_size + 1) % 50 == 0 or (i + batch_size) >= len(image_paths):
            elapsed = time.time() - t0
            done = min(i + batch_size, len(image_paths))
            print(f"[{imgsz}px-{view}] {done}/{len(image_paths)} images processed ({elapsed:.1f}s)", flush=True)

    del model
    torch.cuda.empty_cache()
    return results


# ==============================================================================
# 5. Main Pipeline Orchestrator
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Multi-scale & TTA Aerial Vehicle Inference Engine")
    parser.add_argument("--model-type", type=str, required=True, choices=["dfine", "yolo"], help="Model architecture")
    parser.add_argument("--weights", type=Path, required=True, help="Path to model weights checkpoint")
    parser.add_argument("--test-dir", type=Path, required=True, help="Directory containing test images")
    parser.add_argument("--sample-sub", type=Path, required=True, help="Path to official sample_submission.csv")
    parser.add_argument("--output", type=Path, default=Path("submission.csv"), help="Target submission.csv output path")
    parser.add_argument("--scales", type=int, nargs="+", default=[1280, 1536, 1792], help="Inference resolution scales")
    parser.add_argument("--nms-iou", type=float, default=0.70, help="Class-aware NMS IoU threshold")
    parser.add_argument("--conf-floor", type=float, default=0.001, help="Confidence threshold floor")
    parser.add_argument("--max-det", type=int, default=1000, help="Max detections per image")
    parser.add_argument("--use-hflip", action="store_true", help="Enable horizontal flip TTA consensus (YOLO best recipe)")
    parser.add_argument("--device", type=int, default=0, help="CUDA device index")
    args = parser.parse_args()

    started = time.time()
    print("=" * 80)
    print(f" EXECUTING MULTI-SCALE INFERENCE PIPELINE")
    print(f" Architecture: {args.model_type.upper()} | Scales: {args.scales} | NMS IoU: {args.nms_iou}")
    print(f" Weights:      {args.weights}")
    print("=" * 80)

    # 1. Load sample submission
    sample_sub = pd.read_csv(args.sample_sub)
    sample_ids = sample_sub["image_id"].tolist()
    test_paths = [args.test_dir / f"{img_id}.jpg" for img_id in sample_ids]

    image_sizes_map = {}
    for p in test_paths:
        with Image.open(p) as img:
            image_sizes_map[p.stem] = img.size

    # 2. Extract multi-scale candidates
    raw_candidates: list[dict[str, object]] = []

    if args.model_type == "dfine":
        batch_sizes = {1280: 4, 1536: 4, 1792: 2}
        for sz in args.scales:
            b_size = batch_sizes.get(sz, 2)
            preds = run_dfine_inference_scale(args.weights, test_paths, imgsz=sz, batch_size=b_size, device_id=args.device)
            raw_candidates.extend(preds)
    else:
        # YOLO Engine
        batch_sizes = {1280: 8, 1536: 8, 1792: 4}
        if args.use_hflip:
            # Execute Best YOLO Recipe: 1536 + 1792 + 1792-HFlip Consensus
            print("Applying YOLO Best Recipe: 1536 + 1792 + 1792-HFlip Consensus...")
            preds_orig_1792 = run_yolo_inference_scale(args.weights, test_paths, imgsz=1792, batch_size=4, view="original", device_id=args.device)
            preds_flip_1792 = run_yolo_inference_scale(args.weights, test_paths, imgsz=1792, batch_size=4, view="hflip", device_id=args.device)
            preds_1536 = run_yolo_inference_scale(args.weights, test_paths, imgsz=1536, batch_size=8, view="original", device_id=args.device)

            # Consensus between 1792 views
            m_1792 = compute_pairwise_matches(preds_orig_1792, preds_flip_1792, iou_threshold=0.70)
            consensus_1792 = build_consensus_candidates(preds_orig_1792, preds_flip_1792, m_1792, agreement_value=0.5)

            # Enhance 1536 with cross-scale matching to 1792
            m_1536_1792 = compute_pairwise_matches(preds_1536, preds_orig_1792, iou_threshold=0.70)
            used_1536 = {l: r for l, r, _ in m_1536_1792}
            preds_1536_enhanced = []
            for idx, r in enumerate(preds_1536):
                row = dict(r)
                if idx in used_1536:
                    r_idx = used_1536[idx]
                    other = preds_orig_1792[r_idx]
                    if int(row["class_id"]) == int(other["class_id"]):
                        row["confidence"] = float(row["confidence"]) * 1.10
                    else:
                        row["confidence"] = float(row["confidence"]) * 0.75
                else:
                    row["confidence"] = float(row["confidence"]) * 0.90
                preds_1536_enhanced.append(row)

            raw_candidates = consensus_1792 + preds_1536_enhanced
        else:
            for sz in args.scales:
                b_size = batch_sizes.get(sz, 4)
                preds = run_yolo_inference_scale(args.weights, test_paths, imgsz=sz, batch_size=b_size, view="original", device_id=args.device)
                raw_candidates.extend(preds)

    print(f"Total merged multi-scale candidates: {len(raw_candidates):,d}")

    # 3. Class-Aware NMS
    print(f"Applying Class-Aware NMS (IoU={args.nms_iou}, conf_floor={args.conf_floor}, max_det={args.max_det})...")
    fused_rows = class_aware_nms(raw_candidates, iou_threshold=args.nms_iou, confidence_floor=args.conf_floor, max_det=args.max_det)
    print(f"Fused detections retained: {len(fused_rows):,d}")

    # 4. Class Remapping & Submission Serialization
    fused_by_image: dict[str, list[dict[str, object]]] = defaultdict(list)
    for r in fused_rows:
        fused_by_image[str(r["image_id"])].append(r)

    submission_records = []
    class_distribution = defaultdict(int)

    for img_id in sample_ids:
        img_size = image_sizes_map[img_id]
        img_detections = fused_by_image.get(img_id, [])

        formatted_boxes = []
        for det in img_detections:
            raw_c = int(det["class_id"])
            if args.model_type == "dfine":
                comp_c = DFINE_RAW_TO_COMP[raw_c]
            else:
                comp_c = raw_c  # YOLO natively trained with competition taxonomy

            label_str = COMP_CLASS_NAMES[comp_c]
            class_distribution[label_str] += 1

            formatted_boxes.append((
                label_str,
                float(det["confidence"]),
                float(det["x1"]),
                float(det["y1"]),
                float(det["x2"]),
                float(det["y2"]),
            ))

        pred_str = format_prediction_string(formatted_boxes, img_size, min_area=0.0)
        submission_records.append({"image_id": img_id, "PredictionString": pred_str})

    df_submission = pd.DataFrame(submission_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df_submission.to_csv(args.output, index=False)
    print(f"Saved submission to {args.output} ({len(df_submission)} rows)")

    # 5. Strict Submission Validation
    validate_submission_file(df_submission, sample_sub, args.test_dir)

    print("\nClass Breakdown in Submission:")
    total_b = sum(class_distribution.values())
    for lbl, count in sorted(class_distribution.items(), key=lambda x: -x[1]):
        print(f"  {lbl:<8}: {count:,d} ({count / total_b * 100:.1f}%)")

    print(f"Completed in {time.time() - started:.1f} seconds.")


if __name__ == "__main__":
    main()
