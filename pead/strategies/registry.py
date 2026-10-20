"""Strategy registry: add a strategy by dropping a module in strategies/equity or strategies/options.

Modules are discovered by walking those packages, so there is no list to maintain -- decorating a
class with `@register` (or calling `register_instance`) is the whole integration.
"""
import importlib
import pkgutil

_REGISTRY: dict = {}
_DISCOVERED = False


def register(cls):
    """Class decorator: instantiate with no arguments and register under cls.name."""
    return register_instance(cls())


def register_instance(strategy):
    if not strategy.name:
        raise ValueError(f"{type(strategy).__name__} has no name")
    if strategy.name in _REGISTRY:
        raise ValueError(f"duplicate strategy name {strategy.name!r}")
    _REGISTRY[strategy.name] = strategy
    return strategy.__class__


def discover():
    global _DISCOVERED
    if _DISCOVERED:
        return
    import pead.strategies.equity as eq_pkg
    import pead.strategies.options as op_pkg
    import pead.strategies.portfolio as pf_pkg
    for pkg in (eq_pkg, op_pkg, pf_pkg):
        for m in pkgutil.iter_modules(pkg.__path__):
            importlib.import_module(f"{pkg.__name__}.{m.name}")
    _DISCOVERED = True


def all_strategies() -> dict:
    discover()
    return dict(_REGISTRY)


def get(name: str):
    discover()
    if name not in _REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name]


def resolve(selectors) -> list:
    """Names / 'all' / 'equity' / 'options' / 'family:<x>' / glob-ish prefix* -> strategy names."""
    discover()
    out = []
    for sel in selectors:
        if sel == "all":
            out += list(_REGISTRY)
        elif sel in ("equity", "options", "portfolio"):
            out += [n for n, s in _REGISTRY.items() if s.asset_class == sel]
        elif sel.startswith("family:"):
            out += [n for n, s in _REGISTRY.items() if s.family == sel[7:]]
        elif sel.endswith("*"):
            out += [n for n in _REGISTRY if n.startswith(sel[:-1])]
        else:
            get(sel)
            out.append(sel)
    seen = set()
    return [n for n in out if not (n in seen or seen.add(n))]
