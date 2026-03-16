from prompts import build_draft_prompt, build_reply_prompt


class TestBuildDraftPrompt:
    def test_userlike_without_history_does_not_reference_chat_history_style(self):
        prompt = build_draft_prompt(has_history=False, style=None)

        assert "The chat history is empty" in prompt
        assert "Since there is no chat history" in prompt
        assert "Mimic the user's writing style from the chat history" not in prompt
        assert "Write naturally and human-like" not in prompt

    def test_userlike_with_history_references_chat_history_style(self):
        prompt = build_draft_prompt(has_history=True, style=None)

        assert "You receive the recent chat history" in prompt
        assert "Mimic the user's writing style from the chat history" in prompt
        assert "Write naturally and human-like" not in prompt

    def test_paranoid_style_included_in_draft_prompt(self):
        """Стиль paranoid добавляет блок про безопасность."""
        prompt = build_draft_prompt(has_history=True, style="paranoid")

        assert "Paranoid Guru" in prompt
        assert "gatekeeper" in prompt
        assert "scam" in prompt


class TestBuildReplyPrompt:
    def test_paranoid_style_included_in_reply_prompt(self):
        """Стиль paranoid добавляет блок про безопасность."""
        prompt = build_reply_prompt(style="paranoid")

        assert "Paranoid Guru" in prompt
        assert "gatekeeper" in prompt
        assert "scam" in prompt

