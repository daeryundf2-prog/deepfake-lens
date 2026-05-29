from __future__ import annotations

import argparse
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

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = _image_records(manifest)
    if not records:
        print("error: manifest has no labeled ai/edited/real image records", file=sys.stderr)
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
    train_loader = torch.utils.data.DataLoader(
        _ManifestImageDataset(train_records, transform=transform, image_module=Image),
        batch_size=args.batch_size,
        shuffle=True,
    )
    val_loader = torch.utils.data.DataLoader(
        _ManifestImageDataset(val_records, transform=transform, image_module=Image),
        batch_size=args.batch_size,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = _build_model(torchvision, torch, args.arch).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    criterion = torch.nn.CrossEntropyLoss()
    history = []

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
        metrics = _evaluate(torch, model, val_loader, device)
        history.append({"epoch": epoch, "train_loss": total_loss / max(1, seen), **metrics})

    state_path = args.out / f"{args.arch}-state.pt"
    script_path = args.out / f"{args.arch}.torchscript"
    profile_path = args.out / "runtime-profile.json"
    torch.save(
        {
            "arch": args.arch,
            "image_size": args.image_size,
            "classes": {"real": 0, "ai": 1, "edited": 1},
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
        "image_size": args.image_size,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "seed": args.seed,
        "records": len(records),
        "train_records": len(train_records),
        "val_records": len(val_records),
        "classes": {"real": 0, "ai": 1, "edited": 1},
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
    def __init__(self, records: list[dict[str, object]], *, transform, image_module) -> None:
        self.records = records
        self.transform = transform
        self.image_module = image_module

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int):
        record = self.records[index]
        image = self.image_module.open(record["path"]).convert("RGB")
        label = 0 if record["label"] == "real" else 1
        return self.transform(image), label


def _image_records(manifest: dict[str, object]) -> list[dict[str, object]]:
    records = manifest.get("records", [])
    if not isinstance(records, list):
        return []
    result = []
    for record in records:
        if not isinstance(record, dict) or record.get("label") not in {"ai", "edited", "real"}:
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


def _evaluate(torch, model, loader, device) -> dict[str, float]:
    model.eval()
    correct = 0
    total = 0
    positives = 0
    predicted_positives = 0
    true_positives = 0
    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            labels = labels.to(device)
            predictions = torch.argmax(model(images), dim=1)
            correct += int((predictions == labels).sum().detach().cpu())
            total += int(labels.numel())
            positives += int((labels == 1).sum().detach().cpu())
            predicted_positives += int((predictions == 1).sum().detach().cpu())
            true_positives += int(((predictions == 1) & (labels == 1)).sum().detach().cpu())
    precision = true_positives / max(1, predicted_positives)
    recall = true_positives / max(1, positives)
    return {"val_accuracy": correct / max(1, total), "val_precision": precision, "val_recall": recall}


if __name__ == "__main__":
    raise SystemExit(main())
