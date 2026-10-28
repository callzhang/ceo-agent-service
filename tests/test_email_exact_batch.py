"""Native finite UID-set reads preserve exact complete selected originals."""
import pytest

from app.email_imap_readonly import ImapReadonlyAdapter
from tests.test_email_exact_source import HEADERS, STRUCTURE, LOCATOR
from tests.test_email_imap_readonly import FakeImapSession


class BatchSession(FakeImapSession):
    def __init__(self, *, late_uid=False, fault=None, structure=STRUCTURE, parts=None):
        super().__init__(headers=HEADERS, bodystructure=structure,
                         section_payloads={'1': b'plain 1'}, search_result=b'1 2')
        self.late_uid = late_uid
        self.fault = fault
        self.parts = parts

    def uid(self, command, *args):
        if command != 'FETCH':
            return super().uid(command, *args)
        sequence, query = args
        self.calls.append(('uid', command, *args))
        result = []
        for uid in reversed(bytes(sequence).split(b',')):
            if self.fault == 'missing' and uid == b'2':
                continue
            individual = FakeImapSession(
                headers=HEADERS.replace(b'source@example.test', b'source'+uid+b'@example.test'),
                bodystructure=self.bodystructure, section_payloads=(
                    {'1': b'plain '+uid+b' UID 999 ) ('} if self.parts is None else
                    {key: value.replace(b'{uid}', uid) for key, value in self.parts.items()}))
            _, records = individual.uid(command, uid, query)
            if self.late_uid and isinstance(records[0], tuple):
                prefix, payload = records[0]
                records = [(prefix.replace(b'UID '+uid+b' ', b''), payload), b' UID '+uid+b')']
            if self.fault == 'duplicate' and uid == b'2':
                records = records + records
            if self.fault == 'extra' and uid == b'2':
                records = [((x[0].replace(b'UID 2 ', b'UID 9 '), x[1]) if isinstance(x, tuple)
                            else x.replace(b'UID 2 ', b'UID 9 ')) for x in records]
            result.extend(records)
        if self.fault == 'generation' and query == '(BODY.PEEK[1])':
            self.uidvalidity = 43
        return 'OK', result


def requests():
    return [(LOCATOR.model_copy(update={'uid': uid, 'rfc_message_id': f'<source{uid}@example.test>'}),
             f'a:message-id:<source{uid}@example.test>') for uid in (1, 2)]


@pytest.mark.parametrize('late_uid', [False, True])
def test_native_batch_matches_scalar_and_binds_literals_to_exact_uid(late_uid):
    expected = {}
    for locator, identity in requests():
        session = BatchSession()
        expected[identity] = ImapReadonlyAdapter(session, account_id='a').fetch_message(
            locator, stable_message_identity=identity)
    session = BatchSession(late_uid=late_uid)
    actual = ImapReadonlyAdapter(session, account_id='a').fetch_messages(requests())
    assert actual == expected
    fetches = [call for call in session.calls if call[0] == 'uid']
    assert len(fetches) == 3
    assert all(call[1] == 'FETCH' and call[2] == b'1,2' for call in fetches)
    assert [call for call in session.calls if call[0] == 'select'] == [
        ('select', 'INBOX', True), ('select', 'INBOX', True)]


def test_missing_batch_uid_does_not_misassociate_neighbor_literal():
    result = ImapReadonlyAdapter(BatchSession(fault='missing'), account_id='a').fetch_messages(requests())
    assert result[requests()[0][1]]['textBody'].startswith('plain 1')
    assert isinstance(result[requests()[1][1]], LookupError)


@pytest.mark.parametrize('fault', ['duplicate', 'extra', 'generation'])
def test_batch_rejects_ambiguous_or_changed_source(fault):
    with pytest.raises(LookupError):
        ImapReadonlyAdapter(BatchSession(fault=fault), account_id='a').fetch_messages(requests())


def test_batch_wrong_account_rejected_before_io():
    session = BatchSession()
    bad = [(requests()[0][0].model_copy(update={'account_id': 'other'}), requests()[0][1])]
    with pytest.raises(ValueError, match='account'):
        ImapReadonlyAdapter(session, account_id='a').fetch_messages(bad)
    assert session.calls == []


def test_source_reader_uses_native_uid_set_for_multiple_originals(tmp_path):
    from types import SimpleNamespace
    from app.email_store import EmailStore
    from app.email_source_reader import EmailSourceReader
    from app.email_canonical_storage import upsert_email_message
    from tests.support.email_native_fixtures import _message

    store = EmailStore(tmp_path / 'batch.sqlite3')
    with store._connect() as db:
        db.execute('begin immediate')
        for locator, identity in requests():
            upsert_email_message(db, _message(identity, account_id='a', folder=locator.folder,
                uidvalidity=locator.uidvalidity, uid=locator.uid, rfc_message_id=locator.rfc_message_id))
    session = BatchSession()
    adapter = ImapReadonlyAdapter(session, account_id='a')
    public = SimpleNamespace(get_email_source_locations=store.get_email_source_locations,
                             get_account=lambda account: {'account_id': account})
    reader = EmailSourceReader(public, source_factory=lambda account: adapter)
    results = reader.read_many('a', [identity for _, identity in requests()])
    assert [row['textBody'] for row in results.values()] == ['plain 1 UID 999 ) (', 'plain 2 UID 999 ) (']
    assert len([call for call in session.calls if call[0] == 'uid']) == 3
    assert session.calls[-1] == ('logout',)


def test_complete_batch_keeps_long_plain_and_html_and_skips_attachments():
    structure = (b'(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "8BIT" 90000 1 NIL NIL NIL)'
        b'("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "8BIT" 90000 1 NIL NIL NIL)'
        b'("APPLICATION" "PDF" ("NAME" "UID 9") NIL NIL "BASE64" 5 NIL '
        b'("ATTACHMENT" ("FILENAME" "UID 9")) NIL NIL) "MIXED")')
    parts = {'1': b'{uid} '+b'full '*20000, '2': b'<p>html {uid}</p>', '3': b'never fetch'}
    session = BatchSession(structure=structure, parts=parts, late_uid=True)
    results = ImapReadonlyAdapter(session, account_id='a').fetch_messages(requests())
    for locator, identity in requests():
        row = results[identity]
        assert row['textBody'].startswith(str(locator.uid)+' full ')
        assert row['textBody'].endswith('html '+str(locator.uid))
        assert len(row['textBody'].encode()) > 65536
        assert row['attachments'][0]['filename'] == 'UID 9'
    assert not any(call[-1] == '(BODY.PEEK[3])' for call in session.calls)


def test_native_batches_are_bounded_to_fifty_and_empty_input_does_no_io():
    session = BatchSession()
    adapter = ImapReadonlyAdapter(session, account_id='a')
    assert adapter.fetch_messages([]) == {} and not session.calls
    values = [(LOCATOR.model_copy(update={'uid': uid, 'rfc_message_id': f'<source{uid}@example.test>'}),
               f'a:message-id:<source{uid}@example.test>') for uid in range(1, 52)]
    assert len(adapter.fetch_messages(values)) == 51
    fetches = [call for call in session.calls if call[0] == 'uid']
    assert len(fetches) == 6
    assert max(len(call[2].split(b',')) for call in fetches) == 50
