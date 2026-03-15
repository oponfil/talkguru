from prompts import build_draft_prompt


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
