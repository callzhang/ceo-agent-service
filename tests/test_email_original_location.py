"""Locate current originals despite provider HEADER-search case semantics."""

import pytest

from app.email_imap_readonly import EmailSourceMessageMissing, ImapReadonlyAdapter
from tests.test_email_exact_source import HEADERS, LOCATOR, STRUCTURE, read
from tests.test_email_imap_readonly import FakeImapSession


MESSAGE_ID_FETCH = '(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])'


class HeaderSession(FakeImapSession):
    def __init__(self, ids):
        super().__init__(headers=HEADERS.replace(b'example.test', b'EXAMPLE.TEST'),
                         bodystructure=STRUCTURE, section_payloads={'1': b'complete body'},
                         uidvalidity=99)
        self.ids = ids

    def uid(self, command, *args):
        if command == 'SEARCH':
            self.calls.append(('uid', command, *args))
            # Real provider: a normalized full ID and even an id-left search
            # miss the original header. ALL is independent of header indexing.
            if args == (None, 'ALL'):
                return 'OK', [' '.join(map(str, self.ids)).encode()]
            return 'OK', [b'']
        if command == 'FETCH' and args[-1] == MESSAGE_ID_FETCH:
            self.calls.append(('uid', command, *args))
            data = []
            for uid in args[0].decode().split(','):
                header = f'Message-ID: {self.ids[int(uid)]}\r\n\r\n'.encode()
                data.extend([(f'1 (UID {uid} BODY[HEADER.FIELDS (MESSAGE-ID)] {{{len(header)}}}'.encode(), header), b')'])
            return 'OK', data
        return super().uid(command, *args)


def test_original_location_uses_canonical_id_not_provider_header_index():
    session = HeaderSession({7: '<source@EXAMPLE.TEST>', 8: '<source@other.test>',
                             9: '<prefixsource@example.test>'})
    adapter = ImapReadonlyAdapter(session, account_id='a')
    moved = adapter.locate_message(LOCATOR, folder='Archive')
    assert moved.uid == 7 and moved.uidvalidity == 99
    assert read(adapter, moved)['textBody'] == 'complete body'
    assert all(call[2] is True for call in session.calls if call[0] == 'select')
    assert all(call[1] in {'SEARCH', 'FETCH'} for call in session.calls if call[0] == 'uid')


@pytest.mark.parametrize(('ids', 'ambiguous'), [
    ({}, False),
    ({7: '<other@example.test>'}, False),
    ({7: '<source@EXAMPLE.TEST>', 8: '<source@example.test>'}, True),
    ({7: '<Source@example.test>'}, False),
])
def test_original_location_requires_one_exact_canonical_identity(ids, ambiguous):
    session = HeaderSession(ids)
    with pytest.raises(LookupError) as caught:
        ImapReadonlyAdapter(session, account_id='a').locate_message(LOCATOR, folder='Archive')
    if ambiguous:
        assert type(caught.value) is LookupError
        assert 'ambiguous' in str(caught.value)
    else:
        assert isinstance(caught.value, EmailSourceMessageMissing)
        assert 'missing' in str(caught.value)
    assert not any('BODY.PEEK[1]' in str(call) for call in session.calls)


def test_original_location_batches_headers_and_checks_generation():
    ids = {uid: f'<unrelated-{uid}@example.test>' for uid in range(1, 122)}
    ids[120] = '<source@EXAMPLE.TEST>'
    session = HeaderSession(ids)
    adapter = ImapReadonlyAdapter(session, account_id='a')
    assert adapter.locate_message(LOCATOR, folder='Archive').uid == 120
    batches = [call for call in session.calls if call[-1] == MESSAGE_ID_FETCH]
    assert len(batches) == 3
    assert max(len(call[2].split(b',')) for call in batches) <= 50
    original = session.uid

    def changed(*args):
        result = original(*args)
        session.uidvalidity = 100
        return result

    session.uid = changed
    with pytest.raises(LookupError, match='generation changed'):
        adapter.locate_message(LOCATOR, folder='Archive')
