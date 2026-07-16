from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def read_doc(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_core_context_documents_cover_architecture_and_implemented_features() -> None:
    required_topics = {
        "docs/ARCHITECTURE.md": [
            "Architecture Overview",
            "mermaid",
            "Reference Baselines",
            "Asia/Jakarta",
            "TELEGRAM_ADMIN_USER_ID",
            "FOR UPDATE SKIP LOCKED",
            "Capture[Capture image]",
            "Heartbeat[Heartbeat]",
            "FrameDiff[frame_diff_v1]",
            "Command[Telegram command]",
            "ClaimExpired[Claim expired images]",
        ],
        "docs/IMPLEMENTED.md": [
            "Implemented Features",
            "Android Camera Agent",
            "Telegram Bot and Commands",
            "Retention and Storage Protection",
            "Current Acceptance Status",
        ],
    }

    for relative_path, topics in required_topics.items():
        document = REPOSITORY_ROOT / relative_path
        assert document.is_file(), relative_path
        text = document.read_text(encoding="utf-8")
        for topic in topics:
            assert topic in text, f"{relative_path} is missing {topic!r}"


def test_agents_file_requires_core_context_docs_before_work() -> None:
    agents = read_doc("AGENTS.md")

    for required_text in (
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/IMPLEMENTED.md",
        "before making code or documentation changes",
    ):
        assert required_text in agents


def test_phase_5_operator_documents_exist_and_cover_required_topics() -> None:
    required_topics = {
        "docs/operator/server-installation.md": [
            "bootstrap-ubuntu.sh",
            "deploy-systemd.sh",
            "verify-foundation.sh",
            "Neon",
            "TELEGRAM_ADMIN_USER_ID",
        ],
        "docs/operator/android-installation.md": [
            "Termux",
            "camera-self-test.sh",
            "Termux:Boot",
            "24-hour validation",
        ],
        "docs/operator/credential-rotation.md": [
            "issue",
            "overlap",
            "revoke",
            "camera-admin.sh",
        ],
        "docs/operator/operations.md": [
            "systemctl",
            "journalctl",
            "retention",
            "export",
            "Asia/Jakarta",
        ],
        "docs/operator/incident-recovery.md": [
            "disk pressure",
            "reconciliation",
            "failed export",
            "quarantine",
        ],
        "docs/operator/acceptance-coverage.md": [
            "Must Have",
            "Automated",
            "Manual",
            "Milestone 9",
        ],
        "docs/operator/soak-test-report.md": [
            "24-hour MVP",
            "seven-day soak",
            "critical consistency",
        ],
    }

    for relative_path, topics in required_topics.items():
        document = REPOSITORY_ROOT / relative_path
        assert document.is_file(), relative_path
        text = document.read_text(encoding="utf-8")
        for topic in topics:
            assert topic in text, f"{relative_path} is missing {topic!r}"


def test_readme_links_to_core_and_operator_documents() -> None:
    readme = read_doc("README.md")

    for relative_path in (
        "docs/ARCHITECTURE.md",
        "docs/IMPLEMENTED.md",
        "docs/operator/server-installation.md",
        "docs/operator/android-installation.md",
        "docs/operator/credential-rotation.md",
        "docs/operator/operations.md",
        "docs/operator/incident-recovery.md",
        "docs/operator/acceptance-coverage.md",
        "docs/operator/soak-test-report.md",
    ):
        assert relative_path in readme
