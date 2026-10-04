"""Julia-1, when installed: it loads, answers from the fixed options, and maps free-text quiz replies."""

import pytest

from engine import content, models, quiz

pytestmark = pytest.mark.julia


@pytest.fixture
def julia_cfg(cfg):
    if models.julia(cfg) is None:
        pytest.skip("Julia-1 not installed")
    return cfg


def test_answers_from_fixed_options(julia_cfg):
    choice, probs = models.julia_choose(julia_cfg, "Nina kizunguzungu", models.INTENT_QUESTION, models.INTENTS)
    assert choice in models.INTENTS and abs(sum(probs.values()) - 1) < 1e-3


def test_quiz_reply_mapping(julia_cfg):
    q = content.quiz_question("q07")
    right = q["options"]["sw"][q["correct"] - 1]
    assert quiz._match_option(julia_cfg, q, "sw", "Nadhani " + right.lower())[0] == q["correct"]
