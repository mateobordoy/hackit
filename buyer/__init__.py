"""Hollerau buyer: a belief-based negotiation agent for the Tacto Negotiation Arena.

Code owns every number and every binding action. Each supplier is modelled as a set of
hypotheses (its floor, how fast it steps down, how much patience it has); every reply narrows
them, and every move is chosen by simulating its outcome against the hypotheses still alive.
"""

__version__ = "0.1.0"
