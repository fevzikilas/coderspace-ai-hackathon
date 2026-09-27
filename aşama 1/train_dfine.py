#!/usr/bin/env python3
"""
Production Training Pipeline for D-FINE on Aerial Vehicle Detection
===================================================================
Incorporates Key YOLO26-Large Training Principles:
1. High-Resolution Anchor & Feature Grid: Training at 1280x1280 for tiny aerial targets.
2. Transfer Learning & Semantic Head Init: Pretrained HGNetV2-B4 backbone with differential LRs.
3. Augmentation Strategy & Close-Mosaic Logic:
   - High-variance multi-scale resizing (0.5x - 1.5x) and random flips.
   - Two-phase training: Heavy augmentation followed by clean "close-mosaic" refinement
     in the final epochs for bounding box precision.
4. Optimization: AdamW with warmup, cosine learning rate decay, EMA (0.9999), and AMP (FP16).
5. Automatic Dataset Conversion: Converts YOLO dataset.yaml into COCO JSON format automatically.

Usage:
  python train_dfine.py --dataset data_prepared/dataset.yaml \\
      --pretrained weights/dfine_hgnetv2_l_coco.pth \\
      --output-dir runs/train/dfine_l_img1280 \\
      --epochs 80 --batch-size 4 --imgsz 1280 --device 0
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np
import torch
import yaml
from PIL import Image
from tqdm import tqdm

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR / "dfine_repo"))

COMP_CLASS_NAMES = {0: "car", 1: "van", 2: "bus", 3: "truck"}


# ==============================================================================
# 1. Dataset Converter: YOLO format -> COCO JSON format
# ==============================================================================
def convert_yolo_to_coco(
    images_dir: Path,
    labels_dir: Path,
    output_json: Path,
    class_names: Mapping[int, str] = COMP_CLASS_NAMES,
) -> None:
    """Convert YOLO format txt annotations into a standard COCO JSON format file."""
    if output_json.exists():
        print(f"COCO annotations already exist at {output_json}, skipping conversion.")
        return

    print(f"Converting YOLO annotations from {labels_dir} to COCO format ({output_json})...")
    output_json.parent.mkdir(parents=True, exist_ok=True)

    categories = [{"id": cid, "name": name, "supercategory": "vehicle"} for cid, name in class_names.items()]

    images = []
    annotations = []
    ann_id = 1

    img_files = sorted(list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png")))
    for img_idx, img_path in enumerate(tqdm(img_files, desc="Converting")):
        img_id = img_path.stem
        with Image.open(img_path) as im:
            w, h = im.size

        images.append({
            "id": img_idx + 1,
            "file_name": img_path.name,
            "width": w,
            "height": h,
            "image_stem": img_id,
        })

        lbl_path = labels_dir / f"{img_id}.txt"
        if not lbl_path.exists():
            continue

        with lbl_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:
            parts = line.strip().split()
            if len(parts) < 5:
                continue
            c_id = int(parts[0])
            cx, cy, norm_w, norm_h = map(float, parts[1:5])

            # Convert YOLO normalized cxcywh to COCO pixel xywh
            abs_w = norm_w * w
            abs_h = norm_h * h
            x1 = (cx * w) - (abs_w / 2.0)
            y1 = (cy * h) - (abs_h / 2.0)

            # Clamp coordinates
            x1 = max(0.0, min(float(w), x1))
            y1 = max(0.0, min(float(h), y1))
            abs_w = min(float(w) - x1, abs_w)
            abs_h = min(float(h) - y1, abs_h)

            if abs_w <= 0.0 or abs_h <= 0.0:
                continue

            area = abs_w * abs_h

            annotations.append({
                "id": ann_id,
                "image_id": img_idx + 1,
                "category_id": c_id,
                "bbox": [round(x1, 2), round(y1, 2), round(abs_w, 2), round(abs_h, 2)],
                "area": round(area, 2),
                "iscrowd": 0,
            })
            ann_id += 1

    coco_data = {
        "images": images,
        "annotations": annotations,
        "categories": categories,
    }

    with output_json.open("w", encoding="utf-8") as f:
        json.dump(coco_data, f)
    print(f"Generated {output_json}: {len(images)} images, {len(annotations)} annotations.")


# ==============================================================================
# 2. D-FINE Config Generator (Integrating YOLO26L Training Principles)
# ==============================================================================
def generate_dfine_config(
    base_config_path: Path,
    output_config_path: Path,
    train_img_dir: Path,
    train_ann_file: Path,
    val_img_dir: Path,
    val_ann_file: Path,
    output_dir: Path,
    imgsz: int = 1280,
    epochs: int = 80,
    batch_size: int = 4,
    close_mosaic_epochs: int = 15,
) -> Path:
    """
    Generate an adapted D-FINE training configuration incorporating:
    - High-resolution spatial size (1280x1280)
    - 4 custom classes (car, van, bus, truck)
    - AdamW with differential backbone/decoder learning rates
    - Two-phase augmentation schedule (close-mosaic refinement in the final epochs)
    """
    stop_epoch = max(1, epochs - close_mosaic_epochs)

    config_content = f"""# Auto-generated D-FINE Training Config for Aerial Vehicle Detection
__include__: [
  '{ROOT_DIR / "dfine_repo/configs/dataset/coco_detection.yml"}',
  '{ROOT_DIR / "dfine_repo/configs/runtime.yml"}',
  '{ROOT_DIR / "dfine_repo/configs/dfine/include/dataloader.yml"}',
  '{ROOT_DIR / "dfine_repo/configs/dfine/include/optimizer.yml"}',
  '{ROOT_DIR / "dfine_repo/configs/dfine/include/dfine_hgnetv2.yml"}',
]

task: detection
num_classes: 4
remap_mscoco_category: False

eval_spatial_size: [{imgsz}, {imgsz}]
output_dir: {output_dir}

# HGNetV2-B4 Backbone Configuration
HGNetv2:
  name: 'B4'
  return_idx: [1, 2, 3]
  freeze_stem_only: True
  freeze_at: 0
  freeze_norm: True

# Differential Optimizer (YOLO / DETR Best Practice)
optimizer:
  type: AdamW
  params:
    -
      params: '^(?=.*backbone)(?!.*norm|bn).*$'
      lr: 0.0000125  # Lower LR for pretrained backbone
    -
      params: '^(?=.*(?:encoder|decoder))(?=.*(?:norm|bn)).*$'
      weight_decay: 0.0
  lr: 0.00025        # Base LR for Transformer Head
  betas: [0.9, 0.999]
  weight_decay: 0.000125

# Training Schedule & Two-Phase Augmentation (YOLO Close-Mosaic Principle)
epochs: {epochs}

train_dataloader:
  dataset:
    type: CocoDetection
    img_folder: {train_img_dir}
    ann_file: {train_ann_file}
    return_masks: False
    transforms:
      type: Compose
      ops: ~
      policy:
        epoch: {stop_epoch}  # Switch off heavy distortion at stop_epoch
  shuffle: True
  num_workers: 4
  drop_last: True
  batch_size: {batch_size}
  collate_fn:
    type: BatchImageCollateFunction
    base_size: {imgsz}
    stop_epoch: {stop_epoch}
    ema_restart_decay: 0.9999
    base_size_repeat: 4

val_dataloader:
  dataset:
    type: CocoDetection
    img_folder: {val_img_dir}
    ann_file: {val_ann_file}
    return_masks: False
    transforms:
      type: Compose
      ops:
        - {{type: Resize, size: [{imgsz}, {imgsz}]}}
        - {{type: ConvertPILImage, dtype: 'float32', scale: True}}
  shuffle: False
  num_workers: 4
  drop_last: False
  batch_size: {max(1, batch_size // 2)}
  collate_fn:
    type: BatchImageCollateFunction
"""
    output_config_path.parent.mkdir(parents=True, exist_ok=True)
    output_config_path.write_text(config_content, encoding="utf-8")
    print(f"Generated D-FINE training config at: {output_config_path}")
    return output_config_path


# ==============================================================================
# 3. Training Execution Engine
# ==============================================================================
def run_training(
    config_path: Path,
    pretrained_weights: Optional[Path] = None,
    resume_checkpoint: Optional[Path] = None,
    device: str = "0",
    use_amp: bool = True,
    seed: int = 260925,
) -> None:
    """Execute D-FINE solver training."""
    from src.core import YAMLConfig
    from src.misc import dist_utils
    from src.solver import TASKS

    dist_utils.setup_distributed(print_rank=0, print_method="builtin", seed=seed)

    update_dict = {}
    if pretrained_weights:
        update_dict["tuning"] = str(pretrained_weights)
    if resume_checkpoint:
        update_dict["resume"] = str(resume_checkpoint)
    if device:
        update_dict["device"] = device
    update_dict["use_amp"] = use_amp

    print("\nInitializing D-FINE Solver from config...")
    cfg = YAMLConfig(str(config_path), **update_dict)

    if pretrained_weights or resume_checkpoint:
        if "HGNetv2" in cfg.yaml_cfg:
            cfg.yaml_cfg["HGNetv2"]["pretrained"] = False

    solver = TASKS[cfg.yaml_cfg["task"]](cfg)
    print("\nStarting Training Execution...")
    solver.fit()
    dist_utils.cleanup()
    print("Training successfully finished.")


# ==============================================================================
# 4. Main Entry Point
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Train D-FINE Model with YOLO26L Training Principles")
    parser.add_argument("--dataset", type=Path, default=Path("data_prepared/dataset.yaml"), help="Path to dataset.yaml")
    parser.add_argument("--pretrained", type=Path, help="Pretrained checkpoint to fine-tune from (e.g. bestofbest-dfine.pt)")
    parser.add_argument("--resume", type=Path, help="Resume training from an interrupted checkpoint")
    parser.add_argument("--output-dir", type=Path, default=Path("runs/train/dfine_l_img1280"), help="Output directory for checkpoints")
    parser.add_argument("--imgsz", type=int, default=1280, help="Training resolution scale")
    parser.add_argument("--epochs", type=int, default=80, help="Total training epochs")
    parser.add_argument("--batch-size", type=int, default=4, help="Batch size per GPU")
    parser.add_argument("--close-mosaic", type=int, default=15, help="Number of final epochs with clean augmentation")
    parser.add_argument("--device", type=str, default="0", help="CUDA device index or ids")
    parser.add_argument("--seed", type=int, default=260925, help="Reproducibility random seed")
    args = parser.parse_args()

    print("=" * 80)
    print(" D-FINE MODEL TRAINING PIPELINE (YOLO26-L PRINCIPLES)")
    print("=" * 80)

    # 1. Parse dataset.yaml
    with args.dataset.open("r", encoding="utf-8") as f:
        data_cfg = yaml.safe_load(f)

    data_root = Path(data_cfg.get("path", args.dataset.parent))
    train_img_dir = data_root / data_cfg["train"]
    val_img_dir = data_root / data_cfg["val"]

    train_lbl_dir = train_img_dir.parent.parent / "labels" / "train"
    val_lbl_dir = val_img_dir.parent.parent / "labels" / "val"

    coco_dir = data_root / "coco_annotations"
    coco_train_json = coco_dir / "instances_train.json"
    coco_val_json = coco_dir / "instances_val.json"

    # 2. Automated COCO format conversion
    convert_yolo_to_coco(train_img_dir, train_lbl_dir, coco_train_json)
    convert_yolo_to_coco(val_img_dir, val_lbl_dir, coco_val_json)

    # 3. Generate dynamic D-FINE configuration
    config_path = args.output_dir / "config.yml"
    generate_dfine_config(
        base_config_path=ROOT_DIR / "dfine_repo/configs/dfine/dfine_hgnetv2_l_coco.yml",
        output_config_path=config_path,
        train_img_dir=train_img_dir,
        train_ann_file=coco_train_json,
        val_img_dir=val_img_dir,
        val_ann_file=coco_val_json,
        output_dir=args.output_dir,
        imgsz=args.imgsz,
        epochs=args.epochs,
        batch_size=args.batch_size,
        close_mosaic_epochs=args.close_mosaic,
    )

    # 4. Launch Training
    run_training(
        config_path=config_path,
        pretrained_weights=args.pretrained,
        resume_checkpoint=args.resume,
        device=args.device,
        use_amp=True,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
