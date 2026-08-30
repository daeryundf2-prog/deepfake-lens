from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optional Deepfake Lens neural detector trainer.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arch", choices=["convnext_tiny", "efficientnet_b0"], default="convnext_tiny")
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=20260525)
    parser.add_argument(
        "--sbi",
        action="store_true",
        help="train with Self-Blended Images: fakes are synthesized from real images only",
    )
    parser.add_argument("--early-stopping-patience", type=int, default=3, help="epochs without validation improvement before stopping (default: 3)")
    parser.add_argument("--min-improvement", type=float, default=1e-4, help="minimum validation change that counts as an improvement (default: 1e-4)")
    parser.add_argument("--selection", choices=["loss", "accuracy"], default="loss", help="validation metric used for best-checkpoint selection (default: loss)")
    parser.add_argument("--lr-schedule", choices=["cosine", "none"], default="cosine", help="learning-rate schedule across epochs (default: cosine)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        import torch
        import torchvision
        from PIL import Image
    except ImportError as exc:
        print(f"error: optional training dependency missing: {exc}", file=sys.stderr)
        print("Install torch, torchvision, and Pillow in an experiment environment.", file=sys.stderr)
        return 2

    if args.sbi:
        try:
            import sbi as sbi_module
        except ImportError as exc:
            print(f"error: SBI module unavailable: {exc}", file=sys.stderr)
            return 2
    else:
        sbi_module = None

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = _image_records(manifest, sbi=args.sbi)
    if not records:
        message = (
            "manifest has no labeled real image records (SBI mode needs only reals)"
            if args.sbi
            else "manifest has no labeled ai/edited/real image records"
        )
        print(f"error: {message}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    train_records, val_records = _split_records(records)
    transform = torchvision.transforms.Compose(
        [
            torchvision.transforms.Resize((args.image_size, args.image_size)),
            torchvision.transforms.ToTensor(),
            torchvision.transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )
    dataset_class = _SbiImageDataset if args.sbi else _ManifestImageDataset
    train_loader = torch.utils.data.DataLoader(
        dataset_class(train_records, transform=transform, image_module=Image, seed=args.seed),
        batch_size=args.batch_size,
        shuffle=True,
    )
    val_loader = torch.utils.data.DataLoader(
        dataset_class(val_records, transform=transform, image_module=Image, seed=args.seed + 1),
        batch_size=args.batch_size,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = _build_model(torchvision, torch, args.arch).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    scheduler = None
    if args.lr_schedule == "cosine":
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    criterion = torch.nn.CrossEntropyLoss()
    history = []

    # Best-checkpoint selection: keep the weights from the best validation
    # epoch instead of whatever the last epoch happened to produce, and stop
    # when validation stops improving.
    best_metric = 0.0
    best_epoch = 0
    best_state = None
    epochs_ran = 0
    early_stopped = False

    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        seen = 0
        for images, labels in train_loader:
            images = images.to(device)
            labels = labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(images)
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            batch_size = int(labels.numel())
            total_loss += float(loss.detach().cpu()) * batch_size
            seen += batch_size
        metrics = _evaluate(torch, model, val_loader, device, criterion)
        history.append({"epoch": epoch, "train_loss": total_loss / max(1, seen), **metrics})
        epochs_ran = epoch

        selection_score = metrics["val_loss"] if args.selection == "loss" else metrics["val_accuracy"]
        if args.selection == "loss":
            improved = best_state is None or selection_score < best_metric - args.min_improvement
        else:
            improved = best_state is None or selection_score > best_metric + args.min_improvement
        if improved:
            best_metric = selection_score
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
        elif epoch - best_epoch >= args.early_stopping_patience:
            early_stopped = True
            break
        if scheduler is not None:
            scheduler.step()

    if best_state is not None:
        model.load_state_dict(best_state)

    classes = {"real": 0, "synthetic-fake": 1} if args.sbi else {"real": 0, "ai": 1, "edited": 1}
    state_path = args.out / f"{args.arch}-state.pt"
    script_path = args.out / f"{args.arch}.torchscript"
    profile_path = args.out / "runtime-profile.json"
    torch.save(
        {
            "arch": args.arch,
            "image_size": args.image_size,
            "classes": classes,
            "model_state_dict": model.state_dict(),
            "history": history,
        },
        state_path,
    )
    model.eval()
    traced = torch.jit.trace(model.cpu(), torch.zeros(1, 3, args.image_size, args.image_size))
    traced.save(str(script_path))
    profile = {
        "version": "runtime-profile-v1",
        "name": f"deepfake-lens {args.arch} local experiment",
        "runtime": "torchscript",
        "checkpoint": script_path.name,
        "input_size": args.image_size,
        "score_index": 1,
        "score_activation": "softmax",
        "threshold": 50,
    }
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metadata = {
        "arch": args.arch,
        "mode": "sbi-v1" if args.sbi else "labeled-v1",
        "image_size": args.image_size,
        "epochs": args.epochs,
        "epochs_ran": epochs_ran,
        "early_stopped": early_stopped,
        "best_epoch": best_epoch,
        "best_selection_score": best_metric if best_state is not None else None,
        "selection": args.selection,
        "lr_schedule": args.lr_schedule,
        "early_stopping_patience": args.early_stopping_patience,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "seed": args.seed,
        "records": len(records),
        "train_records": len(train_records),
        "val_records": len(val_records),
        "classes": classes,
        "artifacts": {
            "state_dict": str(state_path),
            "torchscript": str(script_path),
            "runtime_profile": str(profile_path),
        },
        "history": history,
        "torch": getattr(torch, "__version__", "unknown"),
        "torchvision": getattr(torchvision, "__version__", "unknown"),
        "pillow": getattr(Image, "__version__", "unknown"),
    }
    (args.out / "training-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(args.out),
                "records": len(records),
                "metadata": str(args.out / "training-metadata.json"),
                "runtime_profile": str(profile_path),
                "torchscript": str(script_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


class _ManifestImageDataset:
    def __init__(self, records: list[dict[str, object]], *, transform, image_module, seed: int = 0) -> None:
        self.records = records
        self.transform = transform
        self.image_module = image_module
        self.seed = seed

    def __len__(self) -> int:
        return len(self.records)

    def _load_array(self, record: dict[str, object]):
        import numpy as np

        image = self.image_module.open(record["path"]).convert("RGB")
        return np.asarray(image, dtype=np.float64)

    def __getitem__(self, index: int):
        record = self.records[index]
        image = self.image_module.open(record["path"]).convert("RGB")
        label = 0 if record["label"] == "real" else 1
        return self.transform(image), label


class _SbiImageDataset(_ManifestImageDataset):
    """Each real record yields an original (label 0) and a self-blended fake
    (label 1). Blending is deterministic per index for reproducibility."""

    def __len__(self) -> int:
        return len(self.records) * 2

    def __getitem__(self, index: int):
        import numpy as np
        import sbi as sbi_module

        record = self.records[index // 2]
        array = self._load_array(record)
        if index % 2 == 0:
            label = 0
        else:
            rng = np.random.default_rng(self.seed + index)
            array, _ = sbi_module.self_blended_image(array, rng)
            label = 1
        image = self.image_module.fromarray(np.clip(array, 0, 255).astype("uint8"))
        return self.transform(image), label


def _image_records(manifest: dict[str, object], *, sbi: bool = False) -> list[dict[str, object]]:
    records = manifest.get("records", [])
    if not isinstance(records, list):
        return []
    accepted_labels = {"real"} if sbi else {"ai", "edited", "real"}
    result = []
    for record in records:
        if not isinstance(record, dict) or record.get("label") not in accepted_labels:
            continue
        path = Path(str(record.get("path", "")))
        if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.exists():
            continue
        result.append({"path": str(path), "label": str(record["label"]), "split": str(record.get("split", "unspecified"))})
    return result


def _split_records(records: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    train_records = [record for record in records if record["split"] in {"train", "unspecified"}]
    val_records = [record for record in records if record["split"] in {"val", "test"}]
    if not train_records:
        train_records = list(records)
    if not val_records:
        shuffled = list(train_records)
        random.shuffle(shuffled)
        cut = max(1, int(len(shuffled) * 0.8)) if len(shuffled) > 1 else len(shuffled)
        train_records = shuffled[:cut]
        val_records = shuffled[cut:] or shuffled[:1]
    return train_records, val_records


def _build_model(torchvision, torch, arch: str):
    try:
        return torchvision.models.get_model(arch, weights=None, num_classes=2)
    except Exception:
        pass
    if arch == "efficientnet_b0":
        model = torchvision.models.efficientnet_b0(weights=None)
        model.classifier[-1] = torch.nn.Linear(model.classifier[-1].in_features, 2)
        return model
    model = torchvision.models.convnext_tiny(weights=None)
    model.classifier[-1] = torch.nn.Linear(model.classifier[-1].in_features, 2)
    return model


def _evaluate(torch, model, loader, device, criterion) -> dict[str, float]:
    model.eval()
    correct = 0
    total = 0
    positives = 0
    predicted_positives = 0
    true_positives = 0
    total_loss = 0.0
    seen = 0
    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            labels = labels.to(device)
            logits = model(images)
            loss = criterion(logits, labels)
            batch_size = int(labels.numel())
            total_loss += float(loss.detach().cpu()) * batch_size
            seen += batch_size
            predictions = torch.argmax(logits, dim=1)
            correct += int((predictions == labels).sum().detach().cpu())
            total += batch_size
            positives += int((labels == 1).sum().detach().cpu())
            predicted_positives += int((predictions == 1).sum().detach().cpu())
            true_positives += int(((predictions == 1) & (labels == 1)).sum().detach().cpu())
    precision = true_positives / max(1, predicted_positives)
    recall = true_positives / max(1, positives)
    return {
        "val_loss": total_loss / max(1, seen),
        "val_accuracy": correct / max(1, total),
        "val_precision": precision,
        "val_recall": recall,
    }


if __name__ == "__main__":
    raise SystemExit(main())
