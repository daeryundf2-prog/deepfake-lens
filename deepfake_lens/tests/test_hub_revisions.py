"""Hub downloads outside the profile loader are pinned to a commit (G10).

The model zoo's ``from_pretrained`` calls take ``revision=pin`` since WP-C.
The remaining hub loads — SpeechBrain ECAPA in ``audio.compare_speakers``,
``scripts/build_eval_corpus.py`` (Qwen + Wikipedia) and
``experiments/gen_sdturbo_corpus.py`` (SD-Turbo) — need an explicit 40-hex
commit: there is no default revision, a branch name is refused, and the
commit reaches the hub call. Fake modules stand in for speechbrain,
transformers, datasets, diffusers and torch, so nothing is downloaded.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

from deepfake_lens import audio

REPO_ROOT = Path(__file__).resolve().parents[2]
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"


def _load(relpath: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_modules(**modules: Any) -> Any:
    """Patch sys.modules with simple namespace modules (dotted names allowed)."""
    table: dict[str, Any] = {}
    for dotted, attrs in modules.items():
        module = ModuleType(dotted.replace("__", "."))
        for key, value in attrs.items():
            setattr(module, key, value)
        table[dotted.replace("__", ".")] = module
    return mock.patch.dict(sys.modules, table)


class EcapaRevisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.a = Path(self._tmp.name) / "a.wav"
        self.b = Path(self._tmp.name) / "b.wav"
        env = {key: value for key, value in os.environ.items() if key != audio.ECAPA_REVISION_ENV}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(audio._ECAPA_MODELS.clear)

    def _compare(self, **kwargs: Any) -> tuple[Any, mock.Mock]:
        with mock.patch.object(audio, "_ecapa_speaker_similarity", return_value=None) as ecapa, \
                mock.patch.object(audio, "_extract_features", return_value=None):
            result = audio.compare_speakers(self.a, self.b, **kwargs)
        return result, ecapa

    def test_no_revision_means_no_download_and_a_stated_reason(self) -> None:
        result, ecapa = self._compare()
        ecapa.assert_not_called()
        self.assertIn(audio.ECAPA_UNPINNED_LIMITATION, result.limitations)

    def test_branch_name_is_not_a_pin(self) -> None:
        for value in ("main", "v1.0", SHA[:12], SHA.upper()):
            with self.subTest(revision=value):
                _, ecapa = self._compare(ecapa_revision_value=value)
                ecapa.assert_not_called()

    def test_explicit_revision_and_env_reach_the_loader(self) -> None:
        _, ecapa = self._compare(ecapa_revision_value=SHA)
        ecapa.assert_called_once_with(self.a, self.b, SHA)
        with mock.patch.dict(os.environ, {audio.ECAPA_REVISION_ENV: OTHER_SHA}):
            _, ecapa = self._compare()
            ecapa.assert_called_once_with(self.a, self.b, OTHER_SHA)
            # The explicit argument wins over the environment.
            _, ecapa = self._compare(ecapa_revision_value=SHA)
            ecapa.assert_called_once_with(self.a, self.b, SHA)

    def test_from_hparams_gets_the_revision_and_a_per_revision_savedir(self) -> None:
        recognizer = mock.Mock()
        recognizer.from_hparams.return_value = mock.Mock()
        fetching = {"LocalStrategy": SimpleNamespace(COPY="copy")}
        with _fake_modules(speechbrain={}, speechbrain__inference={}, speechbrain__inference__speaker={"SpeakerRecognition": recognizer},
                           speechbrain__utils={}, speechbrain__utils__fetching=fetching), \
                mock.patch.object(audio, "_ecapa_load_waveform", return_value=None):
            self.assertIsNone(audio._ecapa_speaker_similarity(self.a, self.b, SHA))
            self.assertIsNone(audio._ecapa_speaker_similarity(self.a, self.b, SHA))
        recognizer.from_hparams.assert_called_once()
        kwargs = recognizer.from_hparams.call_args.kwargs
        self.assertEqual(kwargs["source"], audio.ECAPA_HUB_MODEL)
        self.assertEqual(kwargs["revision"], SHA)
        self.assertTrue(kwargs["savedir"].endswith(SHA))
        self.assertIn(SHA, audio._ECAPA_MODELS)

    def test_compare_files_and_cli_pass_the_revision(self) -> None:
        from deepfake_lens.cli_parser import build_parser
        from deepfake_lens.core import compare_files

        with mock.patch.object(audio, "compare_speakers", return_value=audio.SpeakerComparison(0, 1.0, "unknown", "x", [])) as compare:
            compare_files(self.a, self.b, ecapa_revision=SHA)
        self.assertEqual(compare.call_args.kwargs["ecapa_revision_value"], SHA)
        parser, _ = build_parser()
        args = parser.parse_args(["compare", "a.wav", "b.wav", "--ecapa-revision", SHA])
        self.assertEqual(args.ecapa_revision, SHA)


class BuildEvalCorpusRevisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = _load("scripts/build_eval_corpus.py", "build_eval_corpus_g10")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = str(Path(self._tmp.name) / "corpus")

    def _exit_code(self, argv: list[str]) -> int:
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            self.script.main(argv)
        return int(raised.exception.code or 0)

    def test_text_part_without_revisions_is_refused(self) -> None:
        self.assertEqual(self._exit_code(["--out", self.out, "--parts", "text"]), 2)
        self.assertEqual(self._exit_code(["--out", self.out, "--parts", "text", "--qwen-revision", SHA]), 2)

    def test_branch_names_are_refused(self) -> None:
        argv = ["--out", self.out, "--parts", "text", "--qwen-revision", "main", "--wikipedia-revision", SHA]
        self.assertEqual(self._exit_code(argv), 2)

    def test_revisions_reach_the_hub_calls(self) -> None:
        load_dataset = mock.Mock(return_value=[])
        tokenizer, causal_lm = mock.Mock(), mock.Mock()
        with _fake_modules(datasets={"load_dataset": load_dataset}, torch={"float32": "float32", "no_grad": mock.MagicMock()},
                           transformers={"AutoTokenizer": tokenizer, "AutoModelForCausalLM": causal_lm}), \
                contextlib.redirect_stdout(io.StringIO()):
            code = self.script.main(["--out", self.out, "--parts", "text", "--n-human", "0", "--n-ai", "0",
                                     "--qwen-revision", SHA, "--wikipedia-revision", OTHER_SHA])
        self.assertEqual(code, 0)
        self.assertEqual(load_dataset.call_args.kwargs["revision"], OTHER_SHA)
        self.assertEqual(tokenizer.from_pretrained.call_args.kwargs["revision"], SHA)
        self.assertEqual(causal_lm.from_pretrained.call_args.kwargs["revision"], SHA)


class SdTurboCorpusRevisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = _load("experiments/gen_sdturbo_corpus.py", "gen_sdturbo_corpus_g10")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = str(Path(self._tmp.name) / "fake")

    def test_revision_is_required_and_must_be_a_commit(self) -> None:
        for argv in (["--out", self.out], ["--out", self.out, "--revision", "main"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
                self.script.main(argv)
            self.assertEqual(raised.exception.code, 2)

    def test_revision_reaches_from_pretrained(self) -> None:
        pipeline = mock.Mock()
        with _fake_modules(torch={"float32": "float32"}, diffusers={"AutoPipelineForText2Image": pipeline}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.script.main(["--out", self.out, "--count", "0", "--revision", SHA]), 0)
        self.assertEqual(pipeline.from_pretrained.call_args.args[0], self.script.SD_TURBO_MODEL)
        self.assertEqual(pipeline.from_pretrained.call_args.kwargs["revision"], SHA)


if __name__ == "__main__":
    unittest.main()
