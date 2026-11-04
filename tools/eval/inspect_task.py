"""inspect-ai task: Aether's core eval suite (model-only cases).

    inspect eval tools/eval/inspect_task.py --model openai-api/aether/<model-id>

with AETHER_BASE_URL (e.g. http://127.0.0.1:8000/v1) and AETHER_API_KEY set; tools/eval/run.py does
that for you. Needs `pip install inspect-ai openai` in an environment of its own.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from inspect_ai import Task, task  # noqa: E402
from inspect_ai.dataset import Sample  # noqa: E402
from inspect_ai.scorer import Score, Target, accuracy, grouped, scorer, stderr  # noqa: E402
from inspect_ai.solver import TaskState, generate  # noqa: E402

from tools.eval import cases as eval_cases  # noqa: E402


@scorer(metrics=[accuracy(), stderr(), grouped(accuracy(), "category")])
def aether_reply_checks():
    async def score(state: TaskState, target: Target) -> Score:
        case = state.metadata["case"]
        fails = eval_cases.grade_reply(case, state.output.completion)
        return Score(value="C" if not fails else "I",
                     answer=state.output.completion[:300],
                     explanation="; ".join(fails) or "all reply checks passed",
                     metadata={"category": case["category"]})
    return score


@task
def aether_core():
    samples = [Sample(input=c["prompt"], id=c["id"], metadata={"case": c, "category": c["category"]})
               for c in eval_cases.load_api_cases()]
    return Task(dataset=samples, solver=[generate()], scorer=aether_reply_checks())
