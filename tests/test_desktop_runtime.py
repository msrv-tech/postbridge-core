from postbridge.desktop_runtime import build_parser, main


def test_desktop_runtime_api_parser_defaults():
    args = build_parser().parse_args(["api"])

    assert args.command == "api"
    assert args.host == "127.0.0.1"
    assert args.port == 8820


def test_desktop_runtime_worker_parser_can_disable_beat():
    args = build_parser().parse_args(["worker", "--no-beat"])

    assert args.command == "worker"
    assert args.beat is False


def test_desktop_runtime_main_dispatches_selected_command(monkeypatch):
    calls = []
    parser = build_parser()
    args = parser.parse_args(["api"])

    def fake_run_api(parsed):
        calls.append(parsed.command)
        return 0

    args.func = fake_run_api
    monkeypatch.setattr(parser, "parse_args", lambda argv: args)
    monkeypatch.setattr("postbridge.desktop_runtime.build_parser", lambda: parser)

    assert main(["api"]) == 0
    assert calls == ["api"]


def test_desktop_runtime_main_infers_command_from_executable_name(monkeypatch):
    seen = []
    parser = build_parser()
    original_parse_args = parser.parse_args

    def fake_parse_args(argv):
        seen.append(argv)
        args = original_parse_args(argv)
        args.func = lambda _args: 0
        return args

    monkeypatch.setattr(parser, "parse_args", fake_parse_args)
    monkeypatch.setattr("postbridge.desktop_runtime.build_parser", lambda: parser)
    monkeypatch.setattr("sys.argv", ["postbridge-api.exe"])

    assert main() == 0
    assert seen == [["api"]]


def test_desktop_runtime_main_prepends_inferred_command_to_args(monkeypatch):
    seen = []
    parser = build_parser()
    original_parse_args = parser.parse_args

    def fake_parse_args(argv):
        seen.append(argv)
        args = original_parse_args(argv)
        args.func = lambda _args: 0
        return args

    monkeypatch.setattr(parser, "parse_args", fake_parse_args)
    monkeypatch.setattr("postbridge.desktop_runtime.build_parser", lambda: parser)
    monkeypatch.setattr("sys.argv", ["postbridge-api.exe", "--host", "127.0.0.1"])

    assert main() == 0
    assert seen == [["api", "--host", "127.0.0.1"]]
