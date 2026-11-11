"""T0-C / T0-D: owner voice-conversion and speaker-verification interfaces.

No model is installed. These tests pin the honesty guards: the only shipped providers answer
UNAVAILABLE, a provider cannot claim CONVERTED without a named model and real output, consent is
enforced, and identity can never PASS without measured scores plus a validated calibration.
`_StubConverter` / `_StubVerifier` are TEST-ONLY doubles used to exercise the guards; they are not
converters and are not importable from the application.
"""
from pathlib import Path

import pytest

from defence_studio.voice_lab import conversion as C
from defence_studio.voice_lab import verification as V

MODEL = {"model_id": "test-vc", "weights_revision": "abc", "code_commit": "def", "licence": "MIT"}


def _consent_ok(consent_id, scope):
    assert scope == "GENERATION"


def _consent_refused(consent_id, scope):
    raise PermissionError("scope not granted")


@pytest.fixture
def files(tmp_path):
    src, own = tmp_path / "gen.wav", tmp_path / "owner.wav"
    src.write_bytes(b"RIFF....WAVE")
    own.write_bytes(b"RIFF....WAVE")
    return tmp_path, src, own


def _req(src, own):
    return C.ConversionRequest(source_wav=src, owner=C.OwnerVoice("n1", "con1", reference_paths=(own,)))


class _StubConverter:
    provider_id = "stub"

    def __init__(self, model=MODEL, write=True):
        self.model, self.write = model, write

    def describe(self):
        return {"provider_id": self.provider_id}

    def convert(self, request, out_wav):
        if self.write:
            Path(out_wav).write_bytes(b"audio")
        return C.ConversionResult(status="CONVERTED", provider_id=self.provider_id, output_wav=Path(out_wav),
                                  model=self.model)


def test_unavailable_converter_never_produces_audio(files):
    tmp, src, own = files
    conv = C.UnavailableConverter()
    assert conv.describe()["available"] is False
    res = C.run_conversion(conv, _req(src, own), tmp / "out.wav", require_consent=_consent_ok)
    d = res.as_dict()
    assert d["status"] == "UNAVAILABLE" and d["output_wav"] is None and d["quality"] == {} and d["model"] is None
    assert d["errors"][0]["code"] == "VOICE_CONVERSION_UNAVAILABLE" and d["processing_s"] is not None
    assert not (tmp / "out.wav").exists()


def test_conversion_request_and_consent_are_enforced(files):
    tmp, src, own = files
    conv = C.UnavailableConverter()
    assert C.run_conversion(conv, _req(tmp / "nope.wav", own), tmp / "o.wav",
                            require_consent=_consent_ok).errors[0]["code"] == "SOURCE_MISSING"
    no_owner = C.ConversionRequest(source_wav=src, owner=C.OwnerVoice("n1", "con1"))
    assert C.run_conversion(conv, no_owner, tmp / "o.wav", require_consent=_consent_ok).errors[0]["code"] == "OWNER_VOICE_MISSING"
    assert C.run_conversion(conv, _req(src, tmp / "gone.wav"), tmp / "o.wav",
                            require_consent=_consent_ok).errors[0]["code"] == "OWNER_RECORDING_MISSING"
    r = C.run_conversion(conv, _req(src, own), tmp / "o.wav", require_consent=_consent_refused)
    assert r.status == "FAILED" and r.errors[0]["code"] == "CONSENT_NOT_VALID"


@pytest.mark.parametrize("model,write,code", [(None, True, "MODEL_IDENTITY_MISSING"),
                                              ({**MODEL, "licence": ""}, True, "MODEL_IDENTITY_MISSING"),
                                              (MODEL, False, "OUTPUT_MISSING")])
def test_converted_claim_needs_named_model_and_real_output(files, model, write, code):
    tmp, src, own = files
    r = C.run_conversion(_StubConverter(model, write), _req(src, own), tmp / "o.wav", require_consent=_consent_ok)
    assert r.status == "FAILED" and r.errors[0]["code"] == code


def test_well_formed_conversion_is_passed_through_with_similarity_caveat(files):
    tmp, src, own = files
    r = C.run_conversion(_StubConverter(), _req(src, own), tmp / "o.wav", require_consent=_consent_ok)
    assert r.status == "CONVERTED" and r.model == MODEL and r.processing_s is not None
    assert any("speaker verification" in l for l in r.limitations)


# ---------------------------------------------------------------- verification
def _scored(owner, ref, model_id="test-sv"):
    return V.VerificationResult(status="SCORED", provider_id="stub", owner_similarity=owner,
                                reference_similarity=ref, model={"model_id": model_id})


CAL = V.Calibration(model_id="test-sv", owner_min_similarity=0.70, min_owner_minus_reference=0.15,
                    validated=True, evidence_ref="test")


def test_unavailable_verifier_reports_no_scores_and_no_verdict(tmp_path):
    ver = V.UnavailableVerifier()
    res = ver.score(V.VerificationRequest(candidate_wav=tmp_path / "c.wav", owner_enrolment=()))
    j = V.judge(res, CAL)
    assert res.owner_similarity is None and res.reference_similarity is None
    assert j["identity"] == "NOT_EVALUATED" and j["leakage"] == "NOT_EVALUATED"
    assert j["errors"][0]["code"] == "SPEAKER_VERIFICATION_UNAVAILABLE"


@pytest.mark.parametrize("cal", [None, V.Calibration("test-sv", 0.7, 0.15, validated=False, evidence_ref="x"),
                                 V.Calibration("other-model", 0.7, 0.15, validated=True, evidence_ref="x")])
def test_identity_never_passes_without_validated_calibration_for_that_model(cal):
    j = V.judge(_scored(0.95, 0.10), cal)
    assert j["identity"] == "UNCALIBRATED" and j["leakage"] == "UNCALIBRATED" and j["margin"] == pytest.approx(0.85)


def test_leakage_and_identity_verdicts_with_calibration():
    assert V.judge(_scored(0.85, 0.30), CAL)["identity"] == "PASS"
    leak = V.judge(_scored(0.85, 0.75), CAL)
    assert leak["leakage"] == "LEAKAGE_SUSPECTED" and leak["identity"] == "FAIL"
    low = V.judge(_scored(0.60, 0.10), CAL)
    assert low["leakage"] == "NO_LEAKAGE_DETECTED" and low["identity"] == "FAIL"
    no_ref = V.judge(_scored(0.90, None), CAL)
    assert no_ref["leakage"] == "NOT_EVALUATED" and no_ref["identity"] == "FAIL"  # leakage must be measured to pass
    assert V.judge(_scored(float("nan"), 0.1), CAL)["identity"] == "NOT_EVALUATED"
