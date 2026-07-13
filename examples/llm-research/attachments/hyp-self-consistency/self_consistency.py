"""Reproduce the kill: self-consistency's gain is compute, not method.

    python self_consistency.py --n 5                    # the naive comparison
    python self_consistency.py --n 5 --compare matched  # the honest one
"""
import argparse

def evaluate(strategy, budget_tokens):
    """Stub: swap in your eval harness. Returns (accuracy, tokens_per_question)."""
    raise NotImplementedError("wire up your eval here")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--compare", choices=["greedy", "matched"], default="greedy")
    a = ap.parse_args()

    acc_sc, tok_sc = evaluate("self_consistency", budget_tokens=a.n * 284)

    if a.compare == "greedy":
        # THE TRAP: 5 samples vs 1 sample. You are measuring compute, not method.
        acc_base, tok_base = evaluate("greedy", budget_tokens=284)
    else:
        # THE GATE: same token budget on both arms.
        acc_base, tok_base = evaluate("long_cot", budget_tokens=a.n * 284)

    print(f"self-consistency {acc_sc:.3f} @ {tok_sc} tok")
    print(f"baseline         {acc_base:.3f} @ {tok_base} tok")
    print(f"delta            {acc_sc - acc_base:+.3f}")
