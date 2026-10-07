from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


class CommandLayout(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = {}
        self.ids = []
        self.nested_forms = []
        self.feed((Path(__file__).parents[1] / "x2_operator_panel/static/index.html").read_text())

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "form" and any(parent[0] == "form" for parent in self.stack):
            self.nested_forms.append(attributes.get("id"))
        identifier = attributes.get("id")
        if identifier:
            self.ids.append(identifier)
            self.nodes[identifier] = (attributes, list(self.stack))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1][0] == tag, f"Unbalanced {tag}"
        self.stack.pop()

    def inside(self, identifier, container):
        return any(attributes.get("id") == container for _, attributes in self.nodes[identifier][1])


def test_command_cards_separate_controls_without_duplicate_ids_or_nested_forms():
    layout = CommandLayout()
    assert not layout.stack
    assert not layout.nested_forms
    assert not [identifier for identifier, count in Counter(layout.ids).items() if count > 1]
    groups = {
        "execution-controls": ["execution-state", "unlock-execution", "cancel-active"],
        "tasks-commands": ["task-shortcut-buttons", "task-shortcut-management", "task-shortcut-editor",
                           "dock-manipulate-undock", "guided-workflow-status", "stop-guided-workflow",
                           "continue-guided-workflow"],
        "manipulation-commands": ["place-form", "table-profile", "saved-plan-select", "execute-saved-plan",
                                  "reset-manipulation", "recover-empty", "recover-holding", "reload-box-profiles",
                                  "manipulation-task-warning", "continue-manipulation", "cancel-manipulation"],
        "posture-commands": ["posture-form", "move-carry-a", "move-carry-b"],
        "navigation-commands": ["preset-list", "clear-costmaps"],
        "docking-commands": ["docking-profile", "undocking-profile", "check-fine-align", "execute-fine-align",
                             "execute-undock", "cancel-docking-motion"],
    }
    for group, identifiers in groups.items():
        for identifier in identifiers:
            assert layout.inside(identifier, group), identifier
            assert not any(layout.inside(identifier, other) for other in groups if other != group)
    assert "task-shortcut-run" not in layout.nodes
    assert layout.inside("manipulation-commands", "task-command-column")
    assert not layout.inside("tasks-commands", "task-command-column")
    assert not layout.inside("tasks-commands", "motion-command-column")
    motion_cards = ["posture-commands", "docking-commands", "navigation-commands"]
    for identifier in motion_cards:
        assert layout.inside(identifier, "motion-command-column")
    assert [identifier for identifier in layout.ids if identifier in motion_cards] == motion_cards


def test_task_controls_stay_visible_and_details_use_requested_defaults():
    layout = CommandLayout()
    for identifier in ["guided-workflow-status", "stop-guided-workflow", "continue-guided-workflow",
                       "manipulation-task-warning", "continue-manipulation", "cancel-manipulation"]:
        assert not any(tag == "details" for tag, _ in layout.nodes[identifier][1])
    for identifier in ["task-shortcut-select", "posture-wait-for-settle"]:
        details = [attributes for tag, attributes in layout.nodes[identifier][1] if tag == "details"]
        assert details and all("open" not in attributes for attributes in details), identifier
    for identifier in ["saved-plan-select", "reset-manipulation", "recover-empty",
                       "recover-holding", "reload-box-profiles"]:
        details = [attributes for tag, attributes in layout.nodes[identifier][1] if tag == "details"]
        assert details and all("open" in attributes for attributes in details), identifier
    assert "hidden" in layout.nodes["manual-place-fields"][0]
