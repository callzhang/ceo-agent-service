"""Independent actual postfix source SQL oracle; no target helpers imported."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

SOURCE = json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-13-database-redesign-phase5c-postfix-source.json').read_text())['tables']
CHILDREN = ('outbound_postfix_receipts', 'native_reply_dispatches')


@pytest.fixture
def db():
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.execute('pragma foreign_keys=on')
        for source in SOURCE.values():
            for obj in source['objects']:
                connection.execute(obj['sql'])
        yield connection


def parent(db, key='key', channel='dingtalk', body=' raw body ', token=''):
    return db.execute('insert into outbound_postfixes(channel,delivery_key,final_body,feedback_token,postfix_version) values(?,?,?,?,?)',
                      (channel, key, body, token, 'version'))


def child(db, kind, key='key', channel='dingtalk', *, ignore=False):
    fields, values = ['channel', 'delivery_key'], [channel, key]
    if kind == 'outbound_postfix_receipts':
        fields.append('provider_result_json')
        values.append(' raw unparsed provider string ')
    return db.execute(f"insert {'or ignore ' if ignore else ''}into {kind}({','.join(fields)}) values({','.join('?' for _ in fields)})", values)


def test_source_definitions_have_only_the_thirteen_observed_columns(db):
    assert sum(len(value['columns']) for value in SOURCE.values()) == 13
    for kind, source in SOURCE.items():
        assert [list(row) for row in db.execute('pragma table_info('+kind+')')] == source['columns']
        assert [list(row) for row in db.execute('pragma foreign_key_list('+kind+')')] == source['foreign_keys']
        assert not db.execute("select 1 from sqlite_schema where type='trigger'").fetchone()


def test_binary_composite_identity_preserves_case_zeroes_spaces_and_channel(db):
    for key in ('A', 'a', '1', '01', ' key ', '\t', 'key:part', '中文'):
        parent(db, key)
        parent(db, key, channel='wechat')
    assert db.execute('select count(*) from outbound_postfixes').fetchone() == (16,)
    with pytest.raises(sqlite3.IntegrityError):
        parent(db, 'A')


@pytest.mark.parametrize('field,value', [('channel', 'DINGTALK'), ('channel', ''), ('channel', None), ('delivery_key', ''), ('delivery_key', '  '), ('delivery_key', None), ('final_body', ''), ('final_body', '  '), ('final_body', None)])
def test_parent_source_constraints(db, field, value):
    values = dict(channel='dingtalk', delivery_key='key', final_body='body', postfix_version='version')
    values[field] = value
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('insert into outbound_postfixes('+','.join(values)+') values('+','.join('?' for _ in values)+')', tuple(values.values()))


@pytest.mark.parametrize('kind', CHILDREN)
def test_child_fk_checks_both_original_key_components_even_with_ignore(db, kind):
    parent(db)
    with pytest.raises(sqlite3.IntegrityError):
        child(db, kind, channel='wechat', ignore=True)
    with pytest.raises(sqlite3.IntegrityError):
        child(db, kind, key='missing', ignore=True)
    assert child(db, kind).rowcount == 1
    assert child(db, kind, ignore=True).rowcount == 0


@pytest.mark.parametrize('kind', CHILDREN)
def test_referenced_parent_key_changes_are_restricted_and_child_rebinding_is_allowed(db, kind):
    parent(db)
    child(db, kind)
    parent(db, 'other')
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update outbound_postfixes set delivery_key='new' where delivery_key='key'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("delete from outbound_postfixes where delivery_key='key'")
    db.execute(f"update {kind} set delivery_key='other' where delivery_key='key'")
    db.execute("update outbound_postfixes set delivery_key='new' where delivery_key='key'")
    assert db.execute(f'select delivery_key from {kind}').fetchone() == ('other',)


def test_source_body_token_and_receipt_are_not_immutable_or_json_validated(db):
    parent(db, token=' token ')
    child(db, 'outbound_postfix_receipts')
    db.execute("update outbound_postfixes set final_body=' changed body ',feedback_token=' changed token '")
    db.execute("update outbound_postfix_receipts set provider_result_json=' not JSON '")
    assert db.execute('select final_body,feedback_token from outbound_postfixes').fetchone() == (' changed body ', ' changed token ')
    assert db.execute('select provider_result_json from outbound_postfix_receipts').fetchone() == (' not JSON ',)
