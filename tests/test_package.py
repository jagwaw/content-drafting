import content_agent


def test_package_is_importable() -> None:
    assert content_agent.__doc__ is not None
