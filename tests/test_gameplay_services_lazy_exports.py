import importlib
import sys
from contextlib import contextmanager

TARGET_MODULES = (
    "gameplay.services",
    "gameplay.services.jail",
    "gameplay.services.manor",
    "gameplay.services.technology",
    "gameplay.services.technology_helpers",
)
UTILS_TARGET_MODULES = (
    "gameplay.services.utils",
    "gameplay.services.utils.cache",
    "gameplay.services.utils.messages",
    "gameplay.services.utils.notifications",
)


@contextmanager
def isolated_service_imports():
    originals = {name: sys.modules.get(name) for name in TARGET_MODULES}
    root_package = sys.modules.get("gameplay")
    root_had_services = root_package is not None and hasattr(root_package, "services")
    original_root_services = getattr(root_package, "services", None) if root_had_services else None
    package = sys.modules.get("gameplay.services")
    original_attrs = {}
    if package is not None:
        for name in TARGET_MODULES:
            if name == "gameplay.services":
                continue
            attr_name = name.rsplit(".", 1)[-1]
            if hasattr(package, attr_name):
                original_attrs[attr_name] = getattr(package, attr_name)

    for name in TARGET_MODULES:
        sys.modules.pop(name, None)

    try:
        yield
    finally:
        for name in TARGET_MODULES:
            sys.modules.pop(name, None)

        for name, module in originals.items():
            if module is not None:
                sys.modules[name] = module
        if root_package is not None:
            if root_had_services:
                setattr(root_package, "services", original_root_services)
            elif hasattr(root_package, "services"):
                delattr(root_package, "services")
        if package is not None:
            for name in TARGET_MODULES:
                if name == "gameplay.services":
                    continue
                attr_name = name.rsplit(".", 1)[-1]
                if attr_name in original_attrs:
                    setattr(package, attr_name, original_attrs[attr_name])
                elif hasattr(package, attr_name):
                    delattr(package, attr_name)


@contextmanager
def isolated_utils_imports():
    originals = {name: sys.modules.get(name) for name in UTILS_TARGET_MODULES}

    for name in UTILS_TARGET_MODULES:
        sys.modules.pop(name, None)

    try:
        yield
    finally:
        for name in UTILS_TARGET_MODULES:
            sys.modules.pop(name, None)

        for name, module in originals.items():
            if module is not None:
                sys.modules[name] = module


def test_gameplay_services_import_is_lazy():
    with isolated_service_imports():
        services = importlib.import_module("gameplay.services")

        assert "gameplay.services.jail" not in sys.modules
        assert "gameplay.services.manor" not in sys.modules
        assert "gameplay.services.technology" not in sys.modules
        assert "gameplay.services.technology_helpers" not in sys.modules
        assert not hasattr(services, "__getattr__")
        assert "ensure_manor" not in dir(services)


def test_gameplay_services_direct_submodule_import_loads_only_requested_module():
    with isolated_service_imports():
        importlib.import_module("gameplay.services")

        manor_module = importlib.import_module("gameplay.services.manor")
        manor_core_module = importlib.import_module("gameplay.services.manor.core")

        assert manor_module is sys.modules["gameplay.services.manor"]
        assert hasattr(manor_core_module, "ensure_manor")
        assert "gameplay.services.manor" in sys.modules
        assert "gameplay.services.technology" not in sys.modules


def test_gameplay_services_submodule_imports_remain_compatible():
    with isolated_service_imports():
        from gameplay.services import jail as jail_service
        from gameplay.services import technology as technology_service
        from gameplay.services import technology_helpers

        assert jail_service is sys.modules["gameplay.services.jail"]
        assert technology_service is sys.modules["gameplay.services.technology"]
        assert technology_helpers is sys.modules["gameplay.services.technology_helpers"]
        assert "gameplay.services.jail" in sys.modules
        assert "gameplay.services.technology" in sys.modules
        assert "gameplay.services.technology_helpers" in sys.modules


def test_gameplay_services_utils_package_is_not_a_compat_aggregator():
    with isolated_utils_imports():
        utils_pkg = importlib.import_module("gameplay.services.utils")

        assert not hasattr(utils_pkg, "__getattr__")
        assert "create_message" not in dir(utils_pkg)
        assert "notify_user" not in dir(utils_pkg)


def test_gameplay_services_utils_direct_submodule_imports_remain_compatible():
    with isolated_utils_imports():
        from gameplay.services.utils import messages as message_service

        cache_module = importlib.import_module("gameplay.services.utils.cache")

        assert message_service is sys.modules["gameplay.services.utils.messages"]
        assert cache_module is sys.modules["gameplay.services.utils.cache"]
