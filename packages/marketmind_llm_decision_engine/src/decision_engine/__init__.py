"""Il Decision Engine: `DecisionEngine` (`engine.py`) fa girare i portfolio
`model` dovuti e il loro bootstrap, sopra un `DecisionStore` (`store.py`) e
un provider LLM scelto dal portfolio (`llm/`).

Nessun import a cascata qui: i moduli si importano direttamente, così
`repositories/` può usare `decision_engine.trading` senza cicli.
"""
