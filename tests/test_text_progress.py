from types import SimpleNamespace

import pytest

from course_note.llm.service import LLMService
from course_note.models import SlideSegment


class Client:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0
        self.last_response_metadata = None

    def resolve_config(self, *args, **kwargs):
        return SimpleNamespace(model='test-text', api_key='private-key', timeout_seconds=120)

    def chat(self, config, prompt):
        self.calls += 1
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        self.last_response_metadata = {'finish_reason': 'stop', 'usage': {
            'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}
        return result


def segment():
    return SlideSegment(0., 10., 'slide.jpg', 'private transcript', '')


def test_text_progress_precedes_request_and_includes_usage_and_file_log(tmp_path, capsys):
    class ObservedClient(Client):
        def chat(self, config, prompt):
            output = capsys.readouterr().out
            assert '[TEXT_SEGMENT 1/1]' in output
            assert '[TEXT_REQUEST]' in output and 'requests=1' in output
            return super().chat(config, prompt)

    service = LLMService(ObservedClient(['note']), log_path=tmp_path / 'text_generation.log')
    result = service.summarize_segments([segment()], None, 'near_transcript', False)
    assert result[0]['summary'] == 'note'
    output = capsys.readouterr().out
    assert '[TEXT_RESPONSE]' in output and '[TEXT_USAGE]' in output and '[TEXT_DONE]' in output
    log = (tmp_path / 'text_generation.log').read_text(encoding='utf-8')
    assert 'total_tokens=30' in log
    assert 'private-key' not in log and 'private transcript' not in log


def test_course_summary_and_key_terms_have_progress_and_shared_counter(tmp_path, capsys):
    service = LLMService(Client(['note', 'course summary', 'term']), log_path=tmp_path / 'text_generation.log')
    segments = [segment()]
    service.summarize_segments(segments, None, 'summary', False)
    assert service.summarize_course(segments, None) == 'course summary'
    assert service.extract_key_terms(segments, None) == ['term']
    output = capsys.readouterr().out
    assert '[TEXT_COURSE_SUMMARY]' in output and '[TEXT_KEY_TERMS]' in output
    assert 'requests=3' in output


@pytest.mark.parametrize('error,tag', [(RuntimeError('request failed'), 'TEXT_ERROR'), (KeyboardInterrupt(), 'TEXT_CANCELLED')])
def test_failed_text_request_logs_error_without_retry(tmp_path, capsys, error, tag):
    client = Client([error])
    service = LLMService(client, log_path=tmp_path / 'text_generation.log')
    with pytest.raises(type(error)):
        service.summarize_segments([segment()], None, 'summary', False)
    assert client.calls == 1
    assert f'[{tag}]' in capsys.readouterr().out


def test_text_wait_progress_is_visible_until_response(tmp_path, monkeypatch, capsys):
    import builtins
    import threading
    from course_note.llm import service as service_module
    waiting_seen = threading.Event()
    original_print = builtins.print

    def observe_print(*args, **kwargs):
        original_print(*args, **kwargs)
        if args and '[TEXT_WAIT]' in str(args[0]):
            waiting_seen.set()

    class WaitingClient(Client):
        def chat(self, config, prompt):
            assert waiting_seen.wait(2), 'No text progress while waiting'
            return super().chat(config, prompt)

    monkeypatch.setattr(service_module, 'WAIT_LOG_SECONDS', .01)
    monkeypatch.setattr(builtins, 'print', observe_print)
    LLMService(WaitingClient(['note']), log_path=tmp_path / 'text_generation.log').summarize_segments(
        [segment()], None, 'near_transcript', False)
    output = capsys.readouterr().out
    assert '[TEXT_WAIT]' in output and '[TEXT_WAIT]' not in output.split('[TEXT_RESPONSE]', 1)[1]
