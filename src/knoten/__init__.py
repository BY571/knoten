"""knoten — falsification-first research graphs in git.

Nodes are markdown. Edges are typed. Claims must cite the gates they survived.
Git does versioning, branching, PRs and hosting; knoten does the two things git
cannot: enforce the graph's own rules, and traverse the falsification structure.
"""
from .core import Node, load          # noqa: F401
from .validate import Violation, check  # noqa: F401

__version__ = "0.1.0"
