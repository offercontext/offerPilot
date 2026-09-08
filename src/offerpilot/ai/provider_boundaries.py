"""Fixed read-only provider boundary manifest for mechanical source gates."""

NON_AGENT_PROVIDER_CALL_MANIFEST = (
    ("offerpilot/api.py", "test_settings_provider", 1),
    ("offerpilot/api.py", "_generate_conversation_title", 1),
    ("offerpilot/api.py", "_complete_json", 1),
    ("offerpilot/ai/interview_knowledge_capture.py", "generate_interview_knowledge_preview", 2),
    (
        "offerpilot/ai/interview_preparation_proposals.py",
        "generate_interview_preparation_proposal",
        2,
    ),
    ("offerpilot/ai/interview_review_proposals.py", "_complete_interview_review_model", 2),
    ("offerpilot/ai/interview_stories.py", "generate_interview_story_proposal", 1),
    ("offerpilot/ai/material_proposals.py", "generate_material_proposal", 1),
    ("offerpilot/ai/mock_interview.py", "generate_question", 2),
    ("offerpilot/ai/mock_interview.py", "_complete_feedback", 2),
    ("offerpilot/ai/offer_negotiation.py", "generate_offer_negotiation_proposal", 1),
    ("offerpilot/ai/opportunity_fit_reviews.py", "_generate", 1),
    ("offerpilot/ai/workflows.py", "complete_json", 1),
)

RAW_PROVIDER_BOUNDARIES = (
    ("offerpilot/ai/client.py", "ConfiguredAIClient", "_complete_with_provider"),
    ("offerpilot/ai/client.py", "ConfiguredAIClient", "_stream_with_provider"),
    (
        "offerpilot/knowledge/provider.py",
        "LiteLLMKnowledgeBriefProviderClient",
        "complete_once",
    ),
    (
        "offerpilot/context_projector/gateway.py",
        "SingleCandidateAgentTransport",
        "complete_one",
    ),
    (
        "offerpilot/context_projector/gateway.py",
        "SingleCandidateAgentTransport",
        "stream_one",
    ),
)

assert len(NON_AGENT_PROVIDER_CALL_MANIFEST) == 13
assert sum(item[2] for item in NON_AGENT_PROVIDER_CALL_MANIFEST) == 18
assert len(RAW_PROVIDER_BOUNDARIES) == 5

