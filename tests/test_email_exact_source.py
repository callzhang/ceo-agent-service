"""Exact Email originals: full selected body, immutable locator, no mailbox writes."""
import pytest

from app.email_classifier_contracts import EmailProviderLocator
from app.email_imap_readonly import ImapReadonlyAdapter, fallback_stable_message_identity
from tests.test_email_imap_readonly import FakeImapSession


HEADERS = b'From: sender@example.test\r\nTo: target@example.test\r\nSubject: Source\r\nMessage-ID: <source@example.test>\r\n\r\n'
STRUCTURE = b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "8BIT" 100000 1 NIL NIL NIL)'
LOCATOR = EmailProviderLocator(account_id='a', folder='INBOX', uidvalidity=42, uid=1,
                               rfc_message_id='<source@example.test>')


def source(*, headers=HEADERS, body=None, **kwargs):
    session = FakeImapSession(headers=headers, bodystructure=STRUCTURE,
                              section_payloads={'1': body or b'complete body'}, **kwargs)
    return ImapReadonlyAdapter(session, account_id='a'), session


def read(adapter, locator=LOCATOR, identity=None):
    return adapter.fetch_message(locator, stable_message_identity=identity or locator.stable_message_identity)


def test_exact_body_is_complete_while_batch_remains_bounded():
    body = ('source ' * 20000).encode()
    adapter, session = source(body=body)
    result = read(adapter)
    assert result['textBody'] == body.decode().strip()
    assert ('uid', 'FETCH', b'1', '(BODY.PEEK[1])') in session.calls
    assert not any(call[1:2] == ('SEARCH',) for call in session.calls)
    assert all(call[2] is True for call in session.calls if call[0] == 'select')
    assert all(call[1] == 'FETCH' for call in session.calls if call[0] == 'uid')
    batch = adapter.fetch_uid_batch(cursor_uidvalidity=42, last_seen_uid=0)
    assert len(batch.messages[0]['textBody'].encode()) <= 64 * 1024


def test_other_account_refused_before_provider_io():
    adapter, session = source()
    with pytest.raises(ValueError, match='account'):
        read(adapter, LOCATOR.model_copy(update={'account_id': 'other'}))
    assert session.calls == []


def test_uid_generation_change_refused_before_content_read():
    adapter, session = source(uidvalidity=99)
    with pytest.raises(LookupError, match='generation'):
        read(adapter)
    assert not any(call[0] == 'uid' for call in session.calls)


@pytest.mark.parametrize('mismatch', ['uid', 'missing', 'message_id', 'stable_identity'])
def test_exact_source_refuses_missing_or_wrong_identity(mismatch):
    adapter, session = source(headers=HEADERS.replace(b'source@example.test', b'other@example.test')
                              if mismatch == 'message_id' else HEADERS)
    original = session.uid

    def uid(command, *args):
        if mismatch == 'missing':
            return 'OK', [None]
        status, data = original(command, *args)
        if mismatch == 'uid':
            data = [(item[0].replace(b'UID 1 ', b'UID 2 '), item[1]) if isinstance(item, tuple)
                    else item.replace(b'UID 1 ', b'UID 2 ') for item in data]
        return status, data

    session.uid = uid
    with pytest.raises(LookupError):
        read(adapter, identity='a:message-id:<wrong@example.test>' if mismatch == 'stable_identity' else None)


def test_no_message_id_checks_existing_content_identity_from_full_body():
    body = 'x' * 90000
    adapter, _ = source(headers=HEADERS.replace(b'Message-ID: <source@example.test>\r\n', b''), body=body.encode())
    identity = fallback_stable_message_identity({'from': {'email': 'sender@example.test'},
        'toRecipients': [{'email': 'target@example.test'}], 'subject': 'Source', 'textBody': body}, account_id='a')
    locator = LOCATOR.model_copy(update={'rfc_message_id': None})
    assert read(adapter, locator, identity)['stableMessageIdentity'] == identity


def test_generation_changed_during_read_is_not_reported_available():
    adapter, session = source()
    original = session.uid

    def uid(command, *args):
        result = original(command, *args)
        if args[-1] == '(BODY.PEEK[1])':
            session.uidvalidity = 99
        return result

    session.uid = uid
    with pytest.raises(LookupError, match='generation'):
        read(adapter)


def test_complete_multipart_body_skips_attachments_and_ignores_uid_in_their_names():
    from app.email_imap_readonly import ephemeral_body_html
    session = FakeImapSession(headers=HEADERS,
        bodystructure=(b'(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "8BIT" 90000 1 NIL NIL NIL)'
          b'("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "8BIT" 90000 1 NIL NIL NIL)'
          b'("APPLICATION" "PDF" ("NAME" "UID 9") NIL NIL "BASE64" 5 NIL '
          b'("ATTACHMENT" ("FILENAME" "UID 9")) NIL NIL) "MIXED")'),
        section_payloads={'1': b'full plain', '2': b'<p>full html</p>', '3': b'never download'})
    result = read(ImapReadonlyAdapter(session, account_id='a'))
    assert result['textBody'] == 'full plain\nfull html'
    assert ephemeral_body_html(result) == '<p>full html</p>'
    assert result['attachments'][0]['filename'] == 'UID 9'
    assert not any(call[-1] == '(BODY.PEEK[3])' for call in session.calls)


def test_wrong_uid_in_body_response_is_not_hidden_by_correct_headers():
    adapter, session = source(body=b'UID 1')
    original = session.uid

    def uid(command, *args):
        status, data = original(command, *args)
        if args[-1] == '(BODY.PEEK[1])':
            data[0] = (data[0][0].replace(b'UID 1 ', b'UID 2 '), data[0][1])
        return status, data

    session.uid = uid
    with pytest.raises(LookupError, match='UID'):
        read(adapter)


def test_uid_named_mailbox_keyword_is_not_a_fetch_uid_attribute():
    adapter, _ = source(flags=('UID', '9'))
    assert read(adapter)['textBody'] == 'complete body'


def test_unsolicited_metadata_cannot_validate_a_different_literal_uid():
    adapter, session = source()
    original = session.uid

    def uid(command, *args):
        status, data = original(command, *args)
        if args[-1] == '(BODY.PEEK[1])':
            data[0] = (data[0][0].replace(b'UID 1 ', b'UID 2 '), data[0][1])
            data.insert(0, b'1 (UID 1 FLAGS ())')
        return status, data

    session.uid = uid
    with pytest.raises(LookupError, match='UID'):
        read(adapter)


@pytest.mark.parametrize('uids', [b'7', b'7 8', b''])
def test_moved_message_locator_requires_one_exact_message_id_in_observed_folder(uids):
    adapter, session = source(search_result=uids, uidvalidity=99)
    if uids == b'7':
        moved = adapter.locate_message(LOCATOR, folder='Archive')
        assert moved.folder == 'Archive' and moved.uidvalidity == 99 and moved.uid == 7
        assert read(adapter, moved)['stableMessageIdentity'] == LOCATOR.stable_message_identity
    else:
        with pytest.raises(LookupError):
            adapter.locate_message(LOCATOR, folder='Archive')
    assert ('uid', 'SEARCH', None, 'ALL') in session.calls
    if uids != b'7':
        assert not any('BODY.PEEK[1]' in str(call) for call in session.calls)


def test_message_id_search_substring_hit_is_not_an_exact_locator():
    adapter, _ = source(headers=HEADERS.replace(b'<source@example.test>', b'<prefixsource@example.test>'))
    with pytest.raises(LookupError):
        adapter.locate_message(LOCATOR, folder='Archive')


def test_moved_message_without_rfc_message_id_is_not_guessed():
    adapter, session = source()
    with pytest.raises(LookupError):
        adapter.locate_message(LOCATOR.model_copy(update={'rfc_message_id':None}), folder='Archive')
    assert session.calls == []
