#!/usr/bin/env python3
r"""
HPP preflight checker.

Uruchom z katalogu głównego repo:
    py tools\hpp_preflight.py

Opcjonalnie można wskazać inny folder moda:
    py tools\hpp_preflight.py "C:\ścieżka\do\hirki-plus-patch"

Skrypt używa wyłącznie standardowej biblioteki Pythona.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


EXPECTED_ROOT_NAME = "hirki-plus-patch"

FILE_LIST_KEYS = {
    "artifacts",
    "creatures",
    "factions",
    "heroes",
    "objects",
    "skills",
    "spells",
}

TEXT_EXTENSIONS = {
    ".json",
    ".md",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}

CONFLICT_MARKERS = (
    "<<<<<<<",
    "=======",
    ">>>>>>>",
)

TOWN_RULES = (
    {
        "name": "Gold Specialist Halls",
        "relative_path": Path(
            "Mods/towns/Mods/Gold Specialist Halls/"
            "Content/config/factions/GoldSpecialistHalls.json"
        ),
        "building": "cityHall",
        "expected_count": 12,
    },
    {
        "name": "Resource Specialist Silos",
        "relative_path": Path(
            "Mods/towns/Mods/Resource Specialist Silos/"
            "Content/config/factions/ResourceSpecialistSilos.json"
        ),
        "building": "resourceSilo",
        "expected_count": 12,
    },
    {
        "name": "Elemental Ritual",
        "relative_path": Path(
            "Mods/towns/Mods/Elemental Ritual/"
            "Content/config/factions/ElementalRitual.json"
        ),
        "building": "mageGuild3",
        "expected_count": 11,
    },
)

GOLDEN_GOOSE_MOD = Path("Mods/artifacts/Mods/Golden Goose/mod.json")
ESTATES_MOD = Path("Mods/skills/Mods/Estates/mod.json")
ESTATES_DEPENDENCY = "hirki-plus-patch.skills.estates"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def ok(self, message: str) -> None:
        self.passed.append(message)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def iter_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for child in value.values():
            yield child
            yield from iter_values(child)
    elif isinstance(value, list):
        for child in value:
            yield child
            yield from iter_values(child)


def find_nearest_module_root(path: Path, repo_root: Path) -> Path | None:
    current = path.parent
    while True:
        if (current / "mod.json").is_file():
            return current
        if current == repo_root:
            break
        if repo_root not in current.parents:
            break
        current = current.parent
    return None


def check_root(root: Path, report: Report) -> None:
    required = ("mod.json", "Content", "Mods")
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        report.error(
            "Folder nie wygląda jak root HPP. Brakuje: " + ", ".join(missing)
        )
        return

    if root.name != EXPECTED_ROOT_NAME:
        report.warning(
            f'Nazwa folderu root to "{root.name}", a instalacyjna nazwa HPP '
            f'powinna brzmieć "{EXPECTED_ROOT_NAME}". '
            "Może to zmienić ID child modów w ręcznej instalacji."
        )
    else:
        report.ok("Root moda ma prawidłową nazwę hirki-plus-patch.")


def check_conflict_markers(root: Path, report: Report) -> None:
    found: list[str] = []

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue

        for marker in CONFLICT_MARKERS:
            if marker in text:
                found.append(f"{path.relative_to(root)}: {marker}")
                break

    if found:
        report.error(
            "Znaleziono znaczniki konfliktu Git:\n  - " + "\n  - ".join(found)
        )
    else:
        report.ok("Brak znaczników konfliktu Git.")


def check_json_files(root: Path, report: Report) -> dict[Path, Any]:
    parsed: dict[Path, Any] = {}
    failures: list[str] = []

    json_paths = sorted(root.rglob("*.json"))
    for path in json_paths:
        try:
            parsed[path] = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            failures.append(f"{path.relative_to(root)}: {exc}")

    if failures:
        report.error(
            "Niepoprawne pliki JSON:\n  - " + "\n  - ".join(failures)
        )
    else:
        report.ok(f"Wszystkie pliki JSON są poprawne ({len(json_paths)}).")

    return parsed


def check_mod_file_references(
    root: Path,
    parsed: dict[Path, Any],
    report: Report,
) -> None:
    missing: list[str] = []
    checked = 0

    for mod_path in sorted(root.rglob("mod.json")):
        data = parsed.get(mod_path)
        if not isinstance(data, dict):
            continue

        module_root = mod_path.parent
        content_root = module_root / "Content"

        for key in FILE_LIST_KEYS:
            values = data.get(key)
            if values is None:
                continue
            if not isinstance(values, list):
                missing.append(
                    f"{mod_path.relative_to(root)}: pole {key} nie jest listą"
                )
                continue

            for relative in values:
                if not isinstance(relative, str):
                    missing.append(
                        f"{mod_path.relative_to(root)}: {key} zawiera wartość "
                        "niebędącą stringiem"
                    )
                    continue
                checked += 1
                target = content_root / Path(relative)
                if not target.is_file():
                    missing.append(
                        f"{mod_path.relative_to(root)}: {key} -> {relative}"
                    )

        # translation paths can occur inside language objects
        def walk_translation_nodes(node: Any) -> None:
            nonlocal checked
            if isinstance(node, dict):
                translations = node.get("translations")
                if translations is not None:
                    if not isinstance(translations, list):
                        missing.append(
                            f"{mod_path.relative_to(root)}: translations nie jest listą"
                        )
                    else:
                        for relative in translations:
                            if not isinstance(relative, str):
                                missing.append(
                                    f"{mod_path.relative_to(root)}: translations "
                                    "zawiera wartość niebędącą stringiem"
                                )
                                continue
                            checked += 1
                            target = content_root / Path(relative)
                            if not target.is_file():
                                missing.append(
                                    f"{mod_path.relative_to(root)}: "
                                    f"translations -> {relative}"
                                )

                for value in node.values():
                    walk_translation_nodes(value)
            elif isinstance(node, list):
                for value in node:
                    walk_translation_nodes(value)

        walk_translation_nodes(data)

    if missing:
        report.error(
            "Brakujące lub niepoprawne referencje plikowe w mod.json:\n  - "
            + "\n  - ".join(missing)
        )
    else:
        report.ok(
            f"Wszystkie referencje plikowe z mod.json istnieją ({checked})."
        )


def collect_translation_keys(
    root: Path,
    parsed: dict[Path, Any],
) -> dict[str, dict[str, list[Any]]]:
    by_language: dict[str, dict[str, list[Any]]] = {}

    for path, data in parsed.items():
        if "translation" not in {part.lower() for part in path.parts}:
            continue
        if not isinstance(data, dict):
            continue

        language = path.stem.lower()
        target = by_language.setdefault(language, {})
        for key, value in data.items():
            target.setdefault(key, []).append(value)

    return by_language


def collect_hpp_translation_references(
    root: Path,
    parsed: dict[Path, Any],
) -> dict[str, set[Path]]:
    references: dict[str, set[Path]] = {}

    for path, data in parsed.items():
        if "translation" in {part.lower() for part in path.parts}:
            continue

        for value in iter_values(data):
            if isinstance(value, str) and value.startswith("@hpp."):
                key = value[1:]
                references.setdefault(key, set()).add(path.relative_to(root))

    return references


def check_translation_references(
    root: Path,
    parsed: dict[Path, Any],
    report: Report,
) -> None:
    languages = collect_translation_keys(root, parsed)
    references = collect_hpp_translation_references(root, parsed)

    errors: list[str] = []

    for language in ("english", "polish"):
        keys = languages.get(language)
        if keys is None:
            errors.append(f"Brak zestawu tłumaczeń: {language}.json")
            continue

        for key, source_paths in sorted(references.items()):
            if key not in keys:
                locations = ", ".join(str(path) for path in sorted(source_paths))
                errors.append(
                    f"{language}: brak klucza {key} użytego w: {locations}"
                )
                continue

            values = keys[key]
            if values and all(value == "" for value in values):
                locations = ", ".join(str(path) for path in sorted(source_paths))
                errors.append(
                    f"{language}: używany klucz {key} ma pustą wartość "
                    f"(ryzyko pipeline), źródła: {locations}"
                )

    if errors:
        report.error(
            "Problemy z referencjami tłumaczeń @hpp.*:\n  - "
            + "\n  - ".join(errors)
        )
    else:
        report.ok(
            f"Referencje @hpp.* są kompletne i niepuste w EN/PL "
            f"({len(references)} unikalnych kluczy)."
        )


def check_forbidden_silent_empty(root: Path, report: Report) -> None:
    found: list[str] = []

    for path in root.rglob("*.json"):
        text = path.read_text(encoding="utf-8-sig")
        if "silentEmpty" in text:
            found.append(str(path.relative_to(root)))

    if found:
        report.error(
            "Znaleziono zakazany wzorzec silentEmpty:\n  - "
            + "\n  - ".join(found)
        )
    else:
        report.ok("Brak zakazanego wzorca silentEmpty.")


def get_building_configuration(
    faction_data: Any,
    building: str,
) -> dict[str, Any] | None:
    if not isinstance(faction_data, dict):
        return None
    try:
        configuration = (
            faction_data["town"]["buildings"][building]["configuration"]
        )
    except (KeyError, TypeError):
        return None
    return configuration if isinstance(configuration, dict) else None


def check_town_rewardables(
    root: Path,
    parsed: dict[Path, Any],
    report: Report,
) -> None:
    errors: list[str] = []

    for rule in TOWN_RULES:
        path = root / rule["relative_path"]
        data = parsed.get(path)

        if not isinstance(data, dict):
            errors.append(f'{rule["name"]}: brak lub niepoprawny plik config')
            continue

        found_count = 0
        for faction_id, faction_data in data.items():
            configuration = get_building_configuration(
                faction_data,
                rule["building"],
            )
            if configuration is None:
                errors.append(
                    f'{rule["name"]}: brak configuration dla {faction_id} / '
                    f'{rule["building"]}'
                )
                continue

            found_count += 1

            if configuration.get("onEmptyMessage") != "":
                errors.append(
                    f'{rule["name"]}: {faction_id} ma onEmptyMessage inne niż ""'
                )

            if configuration.get("onVisitedMessage") != "":
                errors.append(
                    f'{rule["name"]}: {faction_id} ma onVisitedMessage inne niż ""'
                )

            rewards = configuration.get("rewards")
            if not isinstance(rewards, list) or not rewards:
                errors.append(
                    f'{rule["name"]}: {faction_id} nie ma niepustej listy rewards'
                )

        if found_count != rule["expected_count"]:
            errors.append(
                f'{rule["name"]}: znaleziono {found_count} konfiguracji, '
                f'oczekiwano {rule["expected_count"]}'
            )

    if errors:
        report.error(
            "Town rewardables nie spełniają finalnego wzorca fallbacków:\n  - "
            + "\n  - ".join(errors)
        )
    else:
        report.ok(
            "Town rewardables mają literalne puste onEmptyMessage i "
            "onVisitedMessage: Gold 12, Silos 12, Ritual 11."
        )


def check_golden_goose_estates(
    root: Path,
    parsed: dict[Path, Any],
    report: Report,
) -> None:
    goose_path = root / GOLDEN_GOOSE_MOD
    estates_path = root / ESTATES_MOD

    errors: list[str] = []

    goose = parsed.get(goose_path)
    estates = parsed.get(estates_path)

    if not isinstance(goose, dict):
        errors.append(f"Brak lub niepoprawny {GOLDEN_GOOSE_MOD}")
    if not isinstance(estates, dict):
        errors.append(f"Brak lub niepoprawny {ESTATES_MOD}")

    if isinstance(goose, dict):
        depends = goose.get("depends")
        if not isinstance(depends, list):
            errors.append("Golden Goose: depends nie jest listą")
        elif ESTATES_DEPENDENCY not in depends:
            errors.append(
                "Golden Goose nie zależy od "
                f"{ESTATES_DEPENDENCY}"
            )

    if errors:
        report.error(
            "Problem zależności Golden Goose -> Estates:\n  - "
            + "\n  - ".join(errors)
        )
    else:
        report.ok(
            "Golden Goose poprawnie zależy od "
            "hirki-plus-patch.skills.estates, a moduł Estates istnieje."
        )


def print_report(root: Path, report: Report) -> int:
    print("=" * 72)
    print("HPP PREFLIGHT")
    print(f"Root: {root}")
    print("=" * 72)

    for message in report.passed:
        print(f"[OK]   {message}")

    for message in report.warnings:
        print(f"[WARN] {message}")

    for message in report.errors:
        print(f"[FAIL] {message}")

    print("-" * 72)
    print(
        f"Wynik: {len(report.passed)} OK, "
        f"{len(report.warnings)} WARN, "
        f"{len(report.errors)} FAIL"
    )

    if report.errors:
        print("PREFLIGHT FAILED")
        return 1

    print("PREFLIGHT PASSED")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walidacja struktury i krytycznych regresji HPP."
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        help=(
            "Folder root HPP. Domyślnie katalog nadrzędny folderu tools, "
            "w którym znajduje się ten skrypt."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.root is None:
        root = Path(__file__).resolve().parent.parent
    else:
        root = args.root.expanduser().resolve()

    report = Report()

    if not root.is_dir():
        report.error(f"Nie istnieje folder: {root}")
        return print_report(root, report)

    check_root(root, report)
    check_conflict_markers(root, report)

    parsed = check_json_files(root, report)
    if parsed:
        check_mod_file_references(root, parsed, report)
        check_translation_references(root, parsed, report)
        check_forbidden_silent_empty(root, report)
        check_town_rewardables(root, parsed, report)
        check_golden_goose_estates(root, parsed, report)

    return print_report(root, report)


if __name__ == "__main__":
    sys.exit(main())
