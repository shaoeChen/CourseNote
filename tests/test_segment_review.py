import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from course_note.models.core import SlideSegment, TranscriptSegment


def decision(**kw):
    value = dict(status='ok', confidence=.95, same_slide_as_previous=False,
                 capture_index=1, reason='readable matching page', evidence=[],
                 note_mismatch=False, needs_local_analysis=False)
    value.update(kw)
    return value


class Client:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def resolve_vision_config(self, **kw):
        return SimpleNamespace(model='test', api_base='http://test', api_key='secret')

    def chat_images(self, config, prompt, images):
        self.calls.append((prompt, images))
        value = next(self.results)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)


class Reader:
    def __init__(self, quantize=False):
        self.capture_times = {}
        self.calls = []
        self.quantize = quantize

    def __call__(self, video, time, directory):
        self.calls.append(time)
        actual = int(time) if self.quantize else time
        self.capture_times[time] = actual
        path = directory / f'{actual}.jpg'
        path.write_bytes(str(actual).encode())
        return path


def run(tmp_path, results, scenes=None, transcripts=None, notes=None, reader=None):
    from course_note.segmentation.segment_review import SegmentSlideReviewer
    client = Client(results)
    reviewer = SegmentSlideReviewer(client, reader or Reader())
    scenes = scenes or [dict(start_sec=0., end_sec=10.)]
    transcripts = transcripts or [TranscriptSegment(0, 9, 'hello', 'en', 'test')]
    output = reviewer.review(tmp_path / 'video.mp4', scenes, transcripts, tmp_path, existing_notes=notes)
    return output, client, json.loads((tmp_path / 'slide_review.json').read_text())


def test_one_batch_per_segment_and_clean_timestamped_context(tmp_path):
    cues = [TranscriptSegment(0, 2, '<c>hello &amp; world</c>', 'en', 'vtt'),
            TranscriptSegment(2, 4, 'hello & world', 'en', 'vtt'),
            TranscriptSegment(10, 12, 'next topic', 'en', 'vtt')]
    notes = [SlideSegment(0, 10, 'old.png', '', 'existing summary')]
    output, client, report = run(tmp_path, [decision(), decision()],
        [dict(start_sec=0., end_sec=10.), dict(start_sec=10., end_sec=20.)], cues, notes)
    assert len(client.calls) == 2
    assert len(client.calls[0][1]) == 3
    assert len(client.calls[1][1]) == 4
    context = json.loads(client.calls[0][0].split('CONTEXT_JSON\n')[1])
    assert context['transcript'] == [{'start_sec': 0, 'end_sec': 4, 'text': 'hello & world'}]
    assert context['existing_notes'][0]['summary'] == 'existing summary'
    assert context['next_transcript'][0]['text'] == 'next topic'
    assert cues[0].text.startswith('<c>')
    assert output[0]['start_sec'] == 0 and output[-1]['end_sec'] == 20
    assert report['statistics']['planned_base_requests'] == 2


def test_merge_only_explicit_camera_cut_not_same_topic(tmp_path):
    scenes = [dict(start_sec=i * 10., end_sec=(i + 1) * 10.) for i in range(3)]
    output, _, _ = run(tmp_path, [decision(), decision(same_slide_as_previous=True),
        decision(status='camera_cut', same_slide_as_previous=True)], scenes)
    assert [(s['start_sec'], s['end_sec']) for s in output] == [(0, 10), (10, 30)]


def test_unreadable_previous_prevents_merge_and_lowconfidence_has_no_capture(tmp_path):
    output, _, report = run(tmp_path, [decision(status='uncertain', capture_index=None),
        decision(status='camera_cut', same_slide_as_previous=True)],
        [dict(start_sec=0., end_sec=10.), dict(start_sec=10., end_sec=20.)])
    assert len(output) == 2 and 'capture_sec' not in output[0]
    assert report['uncertain_ranges']


def test_slide_ahead_selects_matching_own_frame_without_boundary_shift(tmp_path):
    output, _, _ = run(tmp_path, [decision(status='slide_ahead', capture_index=0)])
    assert 0 <= output[0]['capture_sec'] < 1
    assert output[0]['end_sec'] == 10


def test_cache_reuses_requests_and_invalidates_transcript_and_note(tmp_path):
    notes = [SlideSegment(0, 10, '', '', 'one')]
    run(tmp_path, [decision()], notes=notes)
    _, client, report = run(tmp_path, [], notes=notes)
    assert not client.calls and report['statistics']['cache_hits'] == 1
    changed = [TranscriptSegment(0, 9, 'changed', 'en', 'test')]
    _, client, _ = run(tmp_path, [decision()], transcripts=changed, notes=notes)
    assert len(client.calls) == 1
    notes[0].summary = 'two'
    _, client, _ = run(tmp_path, [decision()], transcripts=changed, notes=notes)
    assert len(client.calls) == 1


def local(parts, confidence=.95):
    return dict(confidence=confidence, reason='two observed pages',
                evidence=[dict(start_sec=0., end_sec=10., detail='observed page change')], parts=parts)


def test_verified_local_split_preserves_coverage_and_uses_actual_frames(tmp_path):
    output, client, report = run(tmp_path, [decision(status='multiple_slides', needs_local_analysis=True),
        local([dict(start_sec=0., end_sec=5., capture_index=1),
               dict(start_sec=5., end_sec=10., capture_index=3)])])
    assert [(s['start_sec'], s['end_sec']) for s in output] == [(0, 5), (5, 10)]
    assert len(client.calls[1][1]) == 5
    assert report['statistics']['local_requests'] == 1
    assert all(s['start_sec'] <= s['capture_sec'] < s['end_sec'] for s in output)


@pytest.mark.parametrize('parts,confidence', [
    ([dict(start_sec=0., end_sec=6., capture_index=1), dict(start_sec=5., end_sec=10., capture_index=3)], .95),
    ([dict(start_sec=0., end_sec=5., capture_index=4), dict(start_sec=5., end_sec=10., capture_index=3)], .95),
    ([dict(start_sec=0., end_sec=5., capture_index=1), dict(start_sec=5., end_sec=10., capture_index=3)], .4),
])
def test_unverified_local_split_keeps_scene(tmp_path, parts, confidence):
    output, _, report = run(tmp_path, [decision(status='multiple_slides', needs_local_analysis=True), local(parts, confidence)])
    assert len(output) == 1 and 'capture_sec' not in output[0]
    assert report['uncertain_ranges']


def test_ten_actual_extra_calls_budget_but_base_continues(tmp_path):
    scenes = [dict(start_sec=i * 10., end_sec=(i + 1) * 10.) for i in range(12)]
    results = []
    for i in range(12):
        results.append(decision(status='content_mismatch', needs_local_analysis=True, capture_index=None))
        if i < 10:
            results.append(local([], .3))
    output, client, report = run(tmp_path, results, scenes)
    assert len(output) == 12 and len(client.calls) == 22
    assert report['statistics']['local_requests'] == 10
    assert any('budget' in r['reason'] for r in report['uncertain_ranges'])


def test_exhausted_local_budget_blocks_unresolved_camera_merge(tmp_path):
    scenes = [dict(start_sec=i * 10., end_sec=(i + 1) * 10.) for i in range(12)]
    results = []
    for _ in range(10):
        results.extend([decision(status='uncertain', capture_index=None, needs_local_analysis=True), local([], .3)])
    results.extend([decision(), decision(status='camera_cut', same_slide_as_previous=True,
        note_mismatch=True, needs_local_analysis=True)])
    output, _, report = run(tmp_path, results, scenes)
    assert len(output) == 12
    assert 'capture_sec' not in output[-1]
    assert 'merged_same_page_camera_cut' not in report['reviews'][-1]['actions']


def test_base_evidence_outside_context_cannot_merge(tmp_path):
    scenes = [dict(start_sec=0., end_sec=10.), dict(start_sec=10., end_sec=20.)]
    output, _, report = run(tmp_path, [decision(), decision(status='camera_cut', same_slide_as_previous=True,
        evidence=[dict(start_sec=5000., end_sec=5001., detail='unrelated evidence')])], scenes)
    assert len(output) == 2 and 'capture_sec' not in output[-1]
    assert any('evidence' in r['reason'] for r in report['uncertain_ranges'])


def test_failure_preserves_partial_report_and_original_files(tmp_path):
    (tmp_path / 'article.md').write_text('unchanged')
    with pytest.raises(RuntimeError, match='backend unavailable'):
        run(tmp_path, [decision(), RuntimeError('backend unavailable')],
            [dict(start_sec=0., end_sec=10.), dict(start_sec=10., end_sec=20.)])
    report = json.loads((tmp_path / 'slide_review.json').read_text())
    assert report['status'] == 'failed' and len(report['reviews']) == 1
    assert (tmp_path / 'article.md').read_text() == 'unchanged'


def test_lowfps_reference_is_reused_by_decoded_time(tmp_path):
    reader = Reader(quantize=True)
    output, client, _ = run(tmp_path, [decision(capture_index=0), decision()],
        [dict(start_sec=0., end_sec=3.), dict(start_sec=3., end_sec=6.)], reader=reader)
    assert output[0]['capture_sec'] == 0
    assert client.calls[1][1][-1] == client.calls[0][1][0]
    assert len(reader.calls) == 6


@pytest.mark.parametrize('invalid', [decision(confidence=True), decision(capture_index=True),
    decision(status='guess'), decision(evidence=[dict(start_sec='00:01', end_sec=2, detail='guess')])])
def test_strict_response_schema_rejects_invalid_types(tmp_path, invalid):
    with pytest.raises(RuntimeError, match='schema'):
        run(tmp_path, [invalid])

def test_local_rejection_cannot_merge_a_camera_cut(tmp_path):
    output, _, report = run(tmp_path, [decision(),
        decision(status='camera_cut', same_slide_as_previous=True, note_mismatch=True, needs_local_analysis=True),
        local([], .3)], [dict(start_sec=0., end_sec=10.), dict(start_sec=10., end_sec=20.)])
    assert len(output) == 2 and 'capture_sec' not in output[1]
    assert report['status'] == 'complete'


def test_cache_local_batches_do_not_spend_extra_call_budget_on_rerun(tmp_path):
    results = [decision(status='multiple_slides', needs_local_analysis=True),
        local([dict(start_sec=0., end_sec=5., capture_index=1),
               dict(start_sec=5., end_sec=10., capture_index=3)])]
    output, _, _ = run(tmp_path, results)
    cached, client, report = run(tmp_path, [])
    assert cached == output and not client.calls
    assert report['statistics']['local_requests'] == 0
    assert report['statistics']['cache_hits'] == 2


def test_decoded_frame_outside_scene_is_never_trusted(tmp_path):
    class OffsetReader(Reader):
        def __call__(self, video, time, directory):
            path = super().__call__(video, time, directory)
            self.capture_times[time] = 10 if time < 10 else time
            return path
    output, _, _ = run(tmp_path, [decision(), decision()],
        [dict(start_sec=0., end_sec=10.), dict(start_sec=10., end_sec=20.)], reader=OffsetReader())
    assert 'capture_sec' not in output[0]


def test_progress_is_visible_before_request_and_saved_to_log(tmp_path, capsys):
    from course_note.segmentation.segment_review import SegmentSlideReviewer

    class ObservedClient(Client):
        def chat_images(self, config, prompt, images):
            output = capsys.readouterr().out
            assert '[SEGMENT 1/1]' in output
            assert '[REQUEST]' in output and 'images=3' in output and 'requests=1' in output
            return super().chat_images(config, prompt, images)

    reviewer = SegmentSlideReviewer(ObservedClient([decision()]), Reader())
    reviewer.review(tmp_path / 'video.mp4', [dict(start_sec=0., end_sec=10.)],
        [TranscriptSegment(0, 9, 'hello', 'en', 'test')], tmp_path)
    output = capsys.readouterr().out
    assert '[RESPONSE]' in output and '[RESULT 1/1]' in output and '[DONE]' in output
    log = (tmp_path / 'review.log').read_text(encoding='utf-8')
    assert '[REQUEST]' in log and '[DONE]' in log and 'secret' not in log


def test_cached_review_logs_zero_new_requests(tmp_path, capsys):
    run(tmp_path, [decision()])
    capsys.readouterr()
    run(tmp_path, [])
    output = capsys.readouterr().out
    assert '[CACHE_HIT]' in output and 'requests=0' in output
    assert '[REQUEST]' not in output


def test_request_failure_is_visible_with_counters(tmp_path, capsys):
    with pytest.raises(RuntimeError, match='backend unavailable'):
        run(tmp_path, [RuntimeError('backend unavailable')])
    output = capsys.readouterr().out
    assert '[ERROR]' in output and 'backend unavailable' in output and 'requests=1' in output


def test_keyboard_interrupt_records_cancelled_state_and_keeps_cache(tmp_path, capsys):
    from course_note.segmentation.segment_review import SegmentSlideReviewer

    class InterruptedClient(Client):
        def chat_images(self, config, prompt, images):
            if len(self.calls) == 1:
                raise KeyboardInterrupt()
            return super().chat_images(config, prompt, images)

    reviewer = SegmentSlideReviewer(InterruptedClient([decision()]), Reader())
    with pytest.raises(KeyboardInterrupt):
        reviewer.review(tmp_path / 'video.mp4',
            [dict(start_sec=0., end_sec=10.), dict(start_sec=10., end_sec=20.)],
            [TranscriptSegment(0, 19, 'hello', 'en', 'test')], tmp_path)
    report = json.loads((tmp_path / 'slide_review.json').read_text())
    assert report['status'] == 'interrupted' and len(report['reviews']) == 1
    assert (tmp_path / 'vision_cache.json').is_file()
    assert '[CANCELLED]' in capsys.readouterr().out


def test_wait_heartbeat_is_visible_during_request_and_stops_after_response(tmp_path, monkeypatch, capsys):
    import builtins
    import threading
    from course_note.segmentation import segment_review

    waiting_seen = threading.Event()
    original_print = builtins.print

    def observe_print(*args, **kwargs):
        original_print(*args, **kwargs)
        if args and '[WAIT]' in str(args[0]):
            waiting_seen.set()

    class WaitingClient(Client):
        def chat_images(self, config, prompt, images):
            assert waiting_seen.wait(2), 'No visible progress while waiting for model'
            return super().chat_images(config, prompt, images)

    monkeypatch.setattr(segment_review, 'WAIT_LOG_SECONDS', .01)
    monkeypatch.setattr(builtins, 'print', observe_print)
    segment_review.SegmentSlideReviewer(WaitingClient([decision()]), Reader()).review(
        tmp_path / 'video.mp4', [dict(start_sec=0., end_sec=10.)],
        [TranscriptSegment(0, 9, 'hello', 'en', 'test')], tmp_path)
    output = capsys.readouterr().out
    assert '[WAIT]' in output and 'elapsed=' in output
    assert '[WAIT]' not in output.split('[RESPONSE]', 1)[1]


def test_invalid_response_is_saved_with_specific_schema_error(tmp_path):
    invalid = decision()
    invalid.pop('confidence')
    with pytest.raises(RuntimeError, match='missing.*confidence'):
        run(tmp_path, [invalid])
    report = json.loads((tmp_path / 'slide_review.json').read_text())
    failed = report['failed_request']
    assert failed['segment_index'] == 0 and failed['kind'] == 'base'
    raw = json.loads(Path(failed['response_path']).read_text(encoding='utf-8'))
    assert 'confidence' not in json.loads(raw['response'])
    assert 'secret' not in Path(failed['response_path']).read_text(encoding='utf-8')


def test_empty_api_final_answer_is_saved_without_automatic_retry(tmp_path):
    from course_note.providers.openai_compatible import LLMResponseError
    from course_note.segmentation.segment_review import SegmentSlideReviewer
    body = json.dumps({'choices': [{'finish_reason': 'length', 'message': {
        'content': '', 'reasoning_content': 'incomplete reasoning'}}]})
    client = Client([LLMResponseError('Empty content; finish_reason=length', body)])
    with pytest.raises(RuntimeError, match='finish_reason=length'):
        SegmentSlideReviewer(client, Reader()).review(tmp_path / 'video.mp4',
            [dict(start_sec=0., end_sec=10.)], [TranscriptSegment(0, 9, 'hello', 'en', 'test')], tmp_path)
    assert len(client.calls) == 1
    report = json.loads((tmp_path / 'slide_review.json').read_text())
    saved = json.loads(Path(report['failed_request']['response_path']).read_text(encoding='utf-8'))
    assert json.loads(saved['response'])['choices'][0]['finish_reason'] == 'length'
