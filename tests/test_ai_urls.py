from postbridge.ai.urls import join_openai_compatible_path


def test_join_openai_compatible_path_accepts_root_base_url():
    assert (
        join_openai_compatible_path("https://provider.example.test/api", "/v1/images/generations")
        == "https://provider.example.test/api/v1/images/generations"
    )


def test_join_openai_compatible_path_accepts_v1_base_url_without_duplicate():
    assert (
        join_openai_compatible_path("https://provider.example.test/api/v1", "/v1/images/generations")
        == "https://provider.example.test/api/v1/images/generations"
    )
