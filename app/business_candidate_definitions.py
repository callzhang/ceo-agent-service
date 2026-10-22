"""Finite, unselected Business candidate declarations."""

from app.action_email_definitions import _encoded_value, _encoded_value_check

SOURCE_PROFILES = {'actual': {'business_attention_items': {'sql': 'CREATE TABLE business_attention_items (\n'
                                                '                    id integer primary key autoincrement,\n'
                                                '                    stable_key text not null unique '
                                                'check(trim(stable_key, '
                                                'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                "<> ''),\n"
                                                '                    category text not null check(category '
                                                "in ('fyi', 'watch', 'decision', 'push')),\n"
                                                "                    status text not null default 'active' "
                                                "check(status in ('active', 'resolved')),\n"
                                                '                    title text not null check(trim(title, '
                                                'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                "<> ''),\n"
                                                '                    business_area text not null default '
                                                "'',\n"
                                                '                    why_attention text not null '
                                                'check(trim(why_attention, '
                                                'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                "<> ''),\n"
                                                '                    current_state text not null '
                                                'check(trim(current_state, '
                                                'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                "<> ''),\n"
                                                '                    ceo_action text not null '
                                                'check(trim(ceo_action, '
                                                'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                "<> ''),\n"
                                                '                    anchor_id integer not null,\n'
                                                '                    evidence_signal_id integer not null,\n'
                                                '                    resolution_signal_id integer,\n'
                                                "                    resolved_at text not null default '',\n"
                                                '                    created_at text not null default '
                                                'current_timestamp,\n'
                                                '                    updated_at text not null default '
                                                'current_timestamp, assessment_json text not null default '
                                                "'{}',\n"
                                                '                    check(\n'
                                                "                        (status = 'active' and "
                                                "resolution_signal_id is null and resolved_at = '')\n"
                                                "                        or (status = 'resolved' and "
                                                'resolution_signal_id is not null\n'
                                                '                            and trim(resolved_at, '
                                                'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                "<> '')\n"
                                                '                    ),\n'
                                                '                    foreign key(anchor_id) references '
                                                'business(anchor_public_id),\n'
                                                '                    foreign key(evidence_signal_id) '
                                                'references business_task_signals(id),\n'
                                                '                    foreign key(resolution_signal_id) '
                                                'references business_task_signals(id)\n'
                                                '                )',
                                         'indexes': ('CREATE INDEX idx_business_attention_items_list\n'
                                                     '                    on '
                                                     'business_attention_items(status, category, updated_at, '
                                                     'id)',),
                                         'triggers': (),
                                         'hash': '31a236b9a5536eb088782791a748674348fc4c2f8e477098060ed9f4a46b471f'},
            'business_project_candidates': {'sql': 'CREATE TABLE business_project_candidates (\n'
                                                   '                    id integer primary key '
                                                   'autoincrement,\n'
                                                   '                    cluster_id integer not null,\n'
                                                   '                    title text not null '
                                                   'check(trim(title, '
                                                   'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                   "<> ''),\n"
                                                   '                    reason text not null '
                                                   'check(trim(reason, '
                                                   'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                   "<> ''),\n"
                                                   '                    status text not null default '
                                                   "'proposed'\n"
                                                   "                        check(status in ('proposed', "
                                                   "'confirmed', 'rejected')),\n"
                                                   '                    confirmed_project_id integer,\n'
                                                   '                    confirmation_signal_id integer,\n'
                                                   '                    created_at text not null default '
                                                   'current_timestamp,\n'
                                                   "                    check((status = 'confirmed') = "
                                                   '(confirmed_project_id is not null)),\n'
                                                   "                    check((status = 'confirmed') = "
                                                   '(confirmation_signal_id is not null)),\n'
                                                   '                    foreign key(cluster_id) references '
                                                   'business(work_cluster_public_id),\n'
                                                   '                    foreign key(confirmed_project_id) '
                                                   'references business(project_public_id),\n'
                                                   '                    foreign key(confirmation_signal_id) '
                                                   'references business_task_signals(id)\n'
                                                   '                )',
                                            'indexes': ('CREATE INDEX '
                                                        'idx_business_project_candidates_cluster\n'
                                                        '                    on '
                                                        'business_project_candidates(cluster_id, status, '
                                                        'id)',),
                                            'triggers': (),
                                            'hash': '7550f4047518bedcbf7336c8f172441a0b67dc4c762e7be41426ced2653b7cf8'},
            'wechat_memory_candidates': {'sql': 'CREATE TABLE wechat_memory_candidates (\n'
                                                '                    id integer primary key autoincrement,\n'
                                                '                    import_run_id text not null,\n'
                                                '                    account_id text not null,\n'
                                                '                    statement text not null,\n'
                                                '                    edited_statement text not null default '
                                                "'',\n"
                                                '                    category text not null,\n'
                                                '                    confidence real not null,\n'
                                                '                    sensitivity text not null,\n'
                                                '                    source_conversation_ids_json text not '
                                                "null default '[]',\n"
                                                '                    source_message_ids_json text not null '
                                                "default '[]',\n"
                                                '                    source_time_start text not null default '
                                                "'',\n"
                                                '                    source_time_end text not null default '
                                                "'',\n"
                                                '                    evidence_excerpt text not null default '
                                                "'',\n"
                                                '                    cleanup_notes text not null default '
                                                "'',\n"
                                                '                    status text not null default '
                                                "'pending',\n"
                                                "                    reviewer text not null default '',\n"
                                                "                    reviewed_at text not null default '',\n"
                                                '                    memory_write_status text not null '
                                                "default '',\n"
                                                "                    memory_id text not null default '',\n"
                                                '                    created_at text not null default '
                                                'current_timestamp,\n'
                                                '                    updated_at text not null default '
                                                'current_timestamp, memory_write_error text not null default '
                                                "'',\n"
                                                '                    unique(import_run_id, statement)\n'
                                                '                )',
                                         'indexes': (),
                                         'triggers': (),
                                         'hash': '24aa599eb77aa428753f8ad024b1787aed5c8229a41a6f975dbc0e0cc8309a95'},
            'business_attention_events': {'sql': 'CREATE TABLE business_attention_events (\n'
                                                 '                    id integer primary key autoincrement,\n'
                                                 '                    attention_item_id integer not null,\n'
                                                 '                    event_type text not null '
                                                 'check(event_type in (\n'
                                                 "                        'opened', 'updated', "
                                                 "'category_changed', 'resolved', 'reopened'\n"
                                                 '                    )),\n'
                                                 '                    signal_id integer not null,\n'
                                                 '                    before_json text not null\n'
                                                 '                        check(json_valid(before_json) and '
                                                 "json_type(before_json) = 'object'),\n"
                                                 '                    after_json text not null\n'
                                                 '                        check(json_valid(after_json) and '
                                                 "json_type(after_json) = 'object'),\n"
                                                 '                    reason text not null '
                                                 'check(trim(reason, '
                                                 'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                 "<> ''),\n"
                                                 '                    created_at text not null default '
                                                 'current_timestamp,\n'
                                                 '                    foreign key(attention_item_id) '
                                                 'references business_attention_items(id),\n'
                                                 '                    foreign key(signal_id) references '
                                                 'business_task_signals(id)\n'
                                                 '                )',
                                          'indexes': ('CREATE INDEX idx_business_attention_events_item\n'
                                                      '                    on '
                                                      'business_attention_events(attention_item_id, '
                                                      'created_at, id)',),
                                          'triggers': (),
                                          'hash': '8833a9d7d410b2319dc4713cdd80a9e4e6f6b9b3247e25ed5928344bd7190c86'},
            'business_attention_proposal_tasks': {'sql': 'CREATE TABLE business_attention_proposal_tasks (\n'
                                                         '                    attention_item_id integer not '
                                                         'null,\n'
                                                         '                    task_id integer not null,\n'
                                                         '                    created_at text not null '
                                                         'default current_timestamp,\n'
                                                         '                    primary key(attention_item_id, '
                                                         'task_id),\n'
                                                         '                    foreign key(attention_item_id) '
                                                         'references business_attention_items(id),\n'
                                                         '                    foreign key(task_id) '
                                                         'references business(task_public_id)\n'
                                                         '                )',
                                                  'indexes': ('CREATE INDEX '
                                                              'idx_business_attention_proposal_tasks_task\n'
                                                              '                    on '
                                                              'business_attention_proposal_tasks(task_id, '
                                                              'attention_item_id)',),
                                                  'triggers': (),
                                                  'hash': 'c9f4ae514ee8dfd38039ff37bad2dc0bb536337779b2c80708c980c6a201c36e'},
            'business_attention_tasks': {'sql': 'CREATE TABLE business_attention_tasks (\n'
                                                '                    attention_item_id integer not null,\n'
                                                '                    task_id integer not null,\n'
                                                '                    created_at text not null default '
                                                'current_timestamp,\n'
                                                '                    primary key(attention_item_id, '
                                                'task_id),\n'
                                                '                    foreign key(attention_item_id) '
                                                'references business_attention_items(id),\n'
                                                '                    foreign key(task_id) references '
                                                'business(task_public_id)\n'
                                                '                )',
                                         'indexes': ('CREATE INDEX idx_business_attention_tasks_task\n'
                                                     '                    on '
                                                     'business_attention_tasks(task_id, attention_item_id)',),
                                         'triggers': (),
                                         'hash': 'fbf49ed0f35dcebe3aa8bb93ce1e3da0f978205f21f22b03e557d7dd7cdb1419'},
            'business_legacy_links': {'sql': 'CREATE TABLE business_legacy_links (\n'
                                             '                    id integer primary key autoincrement,\n'
                                             '                    signal_id integer,\n'
                                             '                    task_id integer,\n'
                                             '                    cluster_id integer,\n'
                                             '                    anchor_id integer,\n'
                                             '                    project_id integer,\n'
                                             '                    project_candidate_id integer,\n'
                                             '                    attention_item_id integer,\n'
                                             '                    work_project_id integer unique,\n'
                                             '                    work_todo_id integer unique,\n'
                                             '                    work_update_id integer unique,\n'
                                             '                    created_at text not null default '
                                             'current_timestamp,\n'
                                             '                    check(\n'
                                             '                        (signal_id is not null) + (task_id is '
                                             'not null) + (cluster_id is not null)\n'
                                             '                        + (anchor_id is not null) + '
                                             '(project_id is not null)\n'
                                             '                        + (project_candidate_id is not null) + '
                                             '(attention_item_id is not null) = 1\n'
                                             '                    ),\n'
                                             '                    check(\n'
                                             '                        (work_project_id is not null) + '
                                             '(work_todo_id is not null)\n'
                                             '                        + (work_update_id is not null) = 1\n'
                                             '                    ),\n'
                                             '                    foreign key(signal_id) references '
                                             'business_task_signals(id),\n'
                                             '                    foreign key(task_id) references '
                                             'business(task_public_id),\n'
                                             '                    foreign key(cluster_id) references '
                                             'business(work_cluster_public_id),\n'
                                             '                    foreign key(anchor_id) references '
                                             'business(anchor_public_id),\n'
                                             '                    foreign key(project_id) references '
                                             'business(project_public_id),\n'
                                             '                    foreign key(project_candidate_id) '
                                             'references business_project_candidates(id),\n'
                                             '                    foreign key(attention_item_id) references '
                                             'business_attention_items(id),\n'
                                             '                    foreign key(work_project_id) references '
                                             'business(work_project_public_id),\n'
                                             '                    foreign key(work_todo_id) references '
                                             'business(work_todo_public_id),\n'
                                             '                    foreign key(work_update_id) references '
                                             'actions(id)\n'
                                             '                )',
                                      'indexes': ('CREATE INDEX idx_business_legacy_links_task\n'
                                                  '                    on business_legacy_links(task_id, '
                                                  'id)',),
                                      'triggers': ('CREATE TRIGGER trg_sync_legacy_link_parent_insert after '
                                                   'insert on business_legacy_links when new.work_update_id '
                                                   'is not null and not exists(select 1 from actions a where '
                                                   'a.id=new.work_update_id and '
                                                   "a.record_type='work_updates') begin select "
                                                   "raise(abort,'work update parent type mismatch'); end",
                                                   'CREATE TRIGGER trg_sync_legacy_link_parent_update after '
                                                   'update of work_update_id on business_legacy_links when '
                                                   'new.work_update_id is not null and not exists(select 1 '
                                                   'from actions a where a.id=new.work_update_id and '
                                                   "a.record_type='work_updates') begin select "
                                                   "raise(abort,'work update parent type mismatch'); end"),
                                      'hash': '2b713425bf64f1b3b0b0b988dcbb5333118ae6c0caf9baa33546e1ae29ed54e9'}},
 'fresh': {'business_attention_items': {'sql': 'CREATE TABLE business_attention_items (\n'
                                               '                    id integer primary key autoincrement,\n'
                                               '                    stable_key text not null unique '
                                               'check(trim(stable_key, '
                                               'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                               "<> ''),\n"
                                               '                    category text not null check(category in '
                                               "('fyi', 'watch', 'decision', 'push')),\n"
                                               "                    status text not null default 'active' "
                                               "check(status in ('active', 'resolved')),\n"
                                               '                    title text not null check(trim(title, '
                                               'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                               "<> ''),\n"
                                               "                    business_area text not null default '',\n"
                                               '                    why_attention text not null '
                                               'check(trim(why_attention, '
                                               'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                               "<> ''),\n"
                                               '                    current_state text not null '
                                               'check(trim(current_state, '
                                               'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                               "<> ''),\n"
                                               '                    ceo_action text not null '
                                               'check(trim(ceo_action, '
                                               'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                               "<> ''),\n"
                                               '                    anchor_id integer not null,\n'
                                               '                    evidence_signal_id integer not null,\n'
                                               '                    assessment_json text not null default '
                                               "'{}',\n"
                                               '                    resolution_signal_id integer,\n'
                                               "                    resolved_at text not null default '',\n"
                                               '                    created_at text not null default '
                                               'current_timestamp,\n'
                                               '                    updated_at text not null default '
                                               'current_timestamp,\n'
                                               '                    check(\n'
                                               "                        (status = 'active' and "
                                               "resolution_signal_id is null and resolved_at = '')\n"
                                               "                        or (status = 'resolved' and "
                                               'resolution_signal_id is not null\n'
                                               '                            and trim(resolved_at, '
                                               'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                               "<> '')\n"
                                               '                    ),\n'
                                               '                    foreign key(anchor_id) references '
                                               'business(anchor_public_id),\n'
                                               '                    foreign key(evidence_signal_id) '
                                               'references business_task_signals(id),\n'
                                               '                    foreign key(resolution_signal_id) '
                                               'references business_task_signals(id)\n'
                                               '                )',
                                        'indexes': ('CREATE INDEX idx_business_attention_items_list\n'
                                                    '                    on business_attention_items(status, '
                                                    'category, updated_at, id)',),
                                        'triggers': (),
                                        'hash': '92717a6fcd349077f8916273af3b416981fef914d6f16272ed5c6ccad5bed605'},
           'business_project_candidates': {'sql': 'CREATE TABLE business_project_candidates (\n'
                                                  '                    id integer primary key '
                                                  'autoincrement,\n'
                                                  '                    cluster_id integer not null,\n'
                                                  '                    title text not null check(trim(title, '
                                                  'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                  "<> ''),\n"
                                                  '                    reason text not null '
                                                  'check(trim(reason, '
                                                  'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                  "<> ''),\n"
                                                  '                    status text not null default '
                                                  "'proposed'\n"
                                                  "                        check(status in ('proposed', "
                                                  "'confirmed', 'rejected')),\n"
                                                  '                    confirmed_project_id integer,\n'
                                                  '                    confirmation_signal_id integer,\n'
                                                  '                    created_at text not null default '
                                                  'current_timestamp,\n'
                                                  "                    check((status = 'confirmed') = "
                                                  '(confirmed_project_id is not null)),\n'
                                                  "                    check((status = 'confirmed') = "
                                                  '(confirmation_signal_id is not null)),\n'
                                                  '                    foreign key(cluster_id) references '
                                                  'business(work_cluster_public_id),\n'
                                                  '                    foreign key(confirmed_project_id) '
                                                  'references business(project_public_id),\n'
                                                  '                    foreign key(confirmation_signal_id) '
                                                  'references business_task_signals(id)\n'
                                                  '                )',
                                           'indexes': ('CREATE INDEX '
                                                       'idx_business_project_candidates_cluster\n'
                                                       '                    on '
                                                       'business_project_candidates(cluster_id, status, '
                                                       'id)',),
                                           'triggers': (),
                                           'hash': '7550f4047518bedcbf7336c8f172441a0b67dc4c762e7be41426ced2653b7cf8'},
           'wechat_memory_candidates': {'sql': 'CREATE TABLE wechat_memory_candidates (\n'
                                               '                    id integer primary key autoincrement,\n'
                                               '                    import_run_id text not null,\n'
                                               '                    account_id text not null,\n'
                                               '                    statement text not null,\n'
                                               '                    edited_statement text not null default '
                                               "'',\n"
                                               '                    category text not null,\n'
                                               '                    confidence real not null,\n'
                                               '                    sensitivity text not null,\n'
                                               '                    source_conversation_ids_json text not '
                                               "null default '[]',\n"
                                               '                    source_message_ids_json text not null '
                                               "default '[]',\n"
                                               '                    source_time_start text not null default '
                                               "'',\n"
                                               '                    source_time_end text not null default '
                                               "'',\n"
                                               '                    evidence_excerpt text not null default '
                                               "'',\n"
                                               "                    cleanup_notes text not null default '',\n"
                                               "                    status text not null default 'pending',\n"
                                               "                    reviewer text not null default '',\n"
                                               "                    reviewed_at text not null default '',\n"
                                               '                    memory_write_status text not null '
                                               "default '',\n"
                                               "                    memory_id text not null default '',\n"
                                               '                    memory_write_error text not null default '
                                               "'',\n"
                                               '                    created_at text not null default '
                                               'current_timestamp,\n'
                                               '                    updated_at text not null default '
                                               'current_timestamp,\n'
                                               '                    unique(import_run_id, statement)\n'
                                               '                )',
                                        'indexes': (),
                                        'triggers': (),
                                        'hash': '5e999cbd903a2b752aef9d4d6788f83bf1fc687f8e52f08835344d600b404814'},
           'business_attention_events': {'sql': 'CREATE TABLE business_attention_events (\n'
                                                '                    id integer primary key autoincrement,\n'
                                                '                    attention_item_id integer not null,\n'
                                                '                    event_type text not null '
                                                'check(event_type in (\n'
                                                "                        'opened', 'updated', "
                                                "'category_changed', 'resolved', 'reopened'\n"
                                                '                    )),\n'
                                                '                    signal_id integer not null,\n'
                                                '                    before_json text not null\n'
                                                '                        check(json_valid(before_json) and '
                                                "json_type(before_json) = 'object'),\n"
                                                '                    after_json text not null\n'
                                                '                        check(json_valid(after_json) and '
                                                "json_type(after_json) = 'object'),\n"
                                                '                    reason text not null check(trim(reason, '
                                                'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
                                                "<> ''),\n"
                                                '                    created_at text not null default '
                                                'current_timestamp,\n'
                                                '                    foreign key(attention_item_id) '
                                                'references business_attention_items(id),\n'
                                                '                    foreign key(signal_id) references '
                                                'business_task_signals(id)\n'
                                                '                )',
                                         'indexes': ('CREATE INDEX idx_business_attention_events_item\n'
                                                     '                    on '
                                                     'business_attention_events(attention_item_id, '
                                                     'created_at, id)',),
                                         'triggers': (),
                                         'hash': '8833a9d7d410b2319dc4713cdd80a9e4e6f6b9b3247e25ed5928344bd7190c86'},
           'business_attention_proposal_tasks': {'sql': 'CREATE TABLE business_attention_proposal_tasks (\n'
                                                        '                    attention_item_id integer not '
                                                        'null,\n'
                                                        '                    task_id integer not null,\n'
                                                        '                    created_at text not null '
                                                        'default current_timestamp,\n'
                                                        '                    primary key(attention_item_id, '
                                                        'task_id),\n'
                                                        '                    foreign key(attention_item_id) '
                                                        'references business_attention_items(id),\n'
                                                        '                    foreign key(task_id) references '
                                                        'business(task_public_id)\n'
                                                        '                )',
                                                 'indexes': ('CREATE INDEX '
                                                             'idx_business_attention_proposal_tasks_task\n'
                                                             '                    on '
                                                             'business_attention_proposal_tasks(task_id, '
                                                             'attention_item_id)',),
                                                 'triggers': (),
                                                 'hash': 'c9f4ae514ee8dfd38039ff37bad2dc0bb536337779b2c80708c980c6a201c36e'},
           'business_attention_tasks': {'sql': 'CREATE TABLE business_attention_tasks (\n'
                                               '                    attention_item_id integer not null,\n'
                                               '                    task_id integer not null,\n'
                                               '                    created_at text not null default '
                                               'current_timestamp,\n'
                                               '                    primary key(attention_item_id, '
                                               'task_id),\n'
                                               '                    foreign key(attention_item_id) '
                                               'references business_attention_items(id),\n'
                                               '                    foreign key(task_id) references '
                                               'business(task_public_id)\n'
                                               '                )',
                                        'indexes': ('CREATE INDEX idx_business_attention_tasks_task\n'
                                                    '                    on '
                                                    'business_attention_tasks(task_id, attention_item_id)',),
                                        'triggers': (),
                                        'hash': 'fbf49ed0f35dcebe3aa8bb93ce1e3da0f978205f21f22b03e557d7dd7cdb1419'},
           'business_legacy_links': {'sql': 'CREATE TABLE business_legacy_links (\n'
                                            '                    id integer primary key autoincrement,\n'
                                            '                    signal_id integer,\n'
                                            '                    task_id integer,\n'
                                            '                    cluster_id integer,\n'
                                            '                    anchor_id integer,\n'
                                            '                    project_id integer,\n'
                                            '                    project_candidate_id integer,\n'
                                            '                    attention_item_id integer,\n'
                                            '                    work_project_id integer unique,\n'
                                            '                    work_todo_id integer unique,\n'
                                            '                    work_update_id integer unique,\n'
                                            '                    created_at text not null default '
                                            'current_timestamp,\n'
                                            '                    check(\n'
                                            '                        (signal_id is not null) + (task_id is '
                                            'not null) + (cluster_id is not null)\n'
                                            '                        + (anchor_id is not null) + (project_id '
                                            'is not null)\n'
                                            '                        + (project_candidate_id is not null) + '
                                            '(attention_item_id is not null) = 1\n'
                                            '                    ),\n'
                                            '                    check(\n'
                                            '                        (work_project_id is not null) + '
                                            '(work_todo_id is not null)\n'
                                            '                        + (work_update_id is not null) = 1\n'
                                            '                    ),\n'
                                            '                    foreign key(signal_id) references '
                                            'business_task_signals(id),\n'
                                            '                    foreign key(task_id) references '
                                            'business(task_public_id),\n'
                                            '                    foreign key(cluster_id) references '
                                            'business(work_cluster_public_id),\n'
                                            '                    foreign key(anchor_id) references '
                                            'business(anchor_public_id),\n'
                                            '                    foreign key(project_id) references '
                                            'business(project_public_id),\n'
                                            '                    foreign key(project_candidate_id) '
                                            'references business_project_candidates(id),\n'
                                            '                    foreign key(attention_item_id) references '
                                            'business_attention_items(id),\n'
                                            '                    foreign key(work_project_id) references '
                                            'business(work_project_public_id),\n'
                                            '                    foreign key(work_todo_id) references '
                                            'business(work_todo_public_id),\n'
                                            '                    foreign key(work_update_id) references '
                                            'actions(id)\n'
                                            '                )',
                                     'indexes': ('CREATE INDEX idx_business_legacy_links_task\n'
                                                 '                    on business_legacy_links(task_id, '
                                                 'id)',),
                                     'triggers': ('CREATE TRIGGER trg_sync_legacy_link_parent_insert after '
                                                  'insert on business_legacy_links when new.work_update_id '
                                                  'is not null and not exists(select 1 from actions a where '
                                                  "a.id=new.work_update_id and a.record_type='work_updates') "
                                                  "begin select raise(abort,'work update parent type "
                                                  "mismatch'); end",
                                                  'CREATE TRIGGER trg_sync_legacy_link_parent_update after '
                                                  'update of work_update_id on business_legacy_links when '
                                                  'new.work_update_id is not null and not exists(select 1 '
                                                  'from actions a where a.id=new.work_update_id and '
                                                  "a.record_type='work_updates') begin select "
                                                  "raise(abort,'work update parent type mismatch'); end"),
                                     'hash': '2b713425bf64f1b3b0b0b988dcbb5333118ae6c0caf9baa33546e1ae29ed54e9'}}}

CANDIDATE_SOURCE_TABLES = {'attention_item': 'business_attention_items',
 'project_candidate': 'business_project_candidates',
 'wechat_memory_candidate': 'wechat_memory_candidates'}

CANDIDATE_FIELDS = {'attention_item': ('id',
                    'stable_key',
                    'category',
                    'status',
                    'title',
                    'business_area',
                    'why_attention',
                    'current_state',
                    'ceo_action',
                    'anchor_id',
                    'evidence_signal_id',
                    'resolution_signal_id',
                    'resolved_at',
                    'created_at',
                    'updated_at',
                    'assessment_json'),
 'project_candidate': ('id',
                       'cluster_id',
                       'title',
                       'reason',
                       'status',
                       'confirmed_project_id',
                       'confirmation_signal_id',
                       'created_at'),
 'wechat_memory_candidate': ('id',
                             'import_run_id',
                             'account_id',
                             'statement',
                             'edited_statement',
                             'category',
                             'confidence',
                             'sensitivity',
                             'source_conversation_ids_json',
                             'source_message_ids_json',
                             'source_time_start',
                             'source_time_end',
                             'evidence_excerpt',
                             'cleanup_notes',
                             'status',
                             'reviewer',
                             'reviewed_at',
                             'memory_write_status',
                             'memory_id',
                             'created_at',
                             'updated_at',
                             'memory_write_error')}

CANDIDATE_FRESH_FIELDS = {'attention_item': ('id',
                    'stable_key',
                    'category',
                    'status',
                    'title',
                    'business_area',
                    'why_attention',
                    'current_state',
                    'ceo_action',
                    'anchor_id',
                    'evidence_signal_id',
                    'assessment_json',
                    'resolution_signal_id',
                    'resolved_at',
                    'created_at',
                    'updated_at'),
 'project_candidate': ('id',
                       'cluster_id',
                       'title',
                       'reason',
                       'status',
                       'confirmed_project_id',
                       'confirmation_signal_id',
                       'created_at'),
 'wechat_memory_candidate': ('id',
                             'import_run_id',
                             'account_id',
                             'statement',
                             'edited_statement',
                             'category',
                             'confidence',
                             'sensitivity',
                             'source_conversation_ids_json',
                             'source_message_ids_json',
                             'source_time_start',
                             'source_time_end',
                             'evidence_excerpt',
                             'cleanup_notes',
                             'status',
                             'reviewer',
                             'reviewed_at',
                             'memory_write_status',
                             'memory_id',
                             'memory_write_error',
                             'created_at',
                             'updated_at')}

CANDIDATE_TYPES = {'attention_item': {'id': 'integer',
                    'stable_key': 'text',
                    'category': 'text',
                    'status': 'text',
                    'title': 'text',
                    'business_area': 'text',
                    'why_attention': 'text',
                    'current_state': 'text',
                    'ceo_action': 'text',
                    'anchor_id': 'integer',
                    'evidence_signal_id': 'integer',
                    'resolution_signal_id': 'integer',
                    'resolved_at': 'text',
                    'created_at': 'text',
                    'updated_at': 'text',
                    'assessment_json': 'text'},
 'project_candidate': {'id': 'integer',
                       'cluster_id': 'integer',
                       'title': 'text',
                       'reason': 'text',
                       'status': 'text',
                       'confirmed_project_id': 'integer',
                       'confirmation_signal_id': 'integer',
                       'created_at': 'text'},
 'wechat_memory_candidate': {'id': 'integer',
                             'import_run_id': 'text',
                             'account_id': 'text',
                             'statement': 'text',
                             'edited_statement': 'text',
                             'category': 'text',
                             'confidence': 'real',
                             'sensitivity': 'text',
                             'source_conversation_ids_json': 'text',
                             'source_message_ids_json': 'text',
                             'source_time_start': 'text',
                             'source_time_end': 'text',
                             'evidence_excerpt': 'text',
                             'cleanup_notes': 'text',
                             'status': 'text',
                             'reviewer': 'text',
                             'reviewed_at': 'text',
                             'memory_write_status': 'text',
                             'memory_id': 'text',
                             'created_at': 'text',
                             'updated_at': 'text',
                             'memory_write_error': 'text'}}

CANDIDATE_NOT_NULL = {'attention_item': ('stable_key',
                    'category',
                    'status',
                    'title',
                    'business_area',
                    'why_attention',
                    'current_state',
                    'ceo_action',
                    'anchor_id',
                    'evidence_signal_id',
                    'resolved_at',
                    'created_at',
                    'updated_at',
                    'assessment_json'),
 'project_candidate': ('cluster_id', 'title', 'reason', 'status', 'created_at'),
 'wechat_memory_candidate': ('import_run_id',
                             'account_id',
                             'statement',
                             'edited_statement',
                             'category',
                             'confidence',
                             'sensitivity',
                             'source_conversation_ids_json',
                             'source_message_ids_json',
                             'source_time_start',
                             'source_time_end',
                             'evidence_excerpt',
                             'cleanup_notes',
                             'status',
                             'reviewer',
                             'reviewed_at',
                             'memory_write_status',
                             'memory_id',
                             'created_at',
                             'updated_at',
                             'memory_write_error')}

CANDIDATE_DEFAULTS = {'attention_item': {'status': "'active'",
                    'business_area': "''",
                    'resolved_at': "''",
                    'created_at': 'current_timestamp',
                    'updated_at': 'current_timestamp',
                    'assessment_json': "'{}'"},
 'project_candidate': {'status': "'proposed'", 'created_at': 'current_timestamp'},
 'wechat_memory_candidate': {'edited_statement': "''",
                             'source_conversation_ids_json': "'[]'",
                             'source_message_ids_json': "'[]'",
                             'source_time_start': "''",
                             'source_time_end': "''",
                             'evidence_excerpt': "''",
                             'cleanup_notes': "''",
                             'status': "'pending'",
                             'reviewer': "''",
                             'reviewed_at': "''",
                             'memory_write_status': "''",
                             'memory_id': "''",
                             'created_at': 'current_timestamp',
                             'updated_at': 'current_timestamp',
                             'memory_write_error': "''"}}

CANDIDATE_KINDS = tuple(CANDIDATE_FIELDS)
CANDIDATE_REFERENCES = {
    'attention_item': (('anchor_id', 'anchor_public_id', 'business'),
                       ('evidence_signal_id', 'id', 'business_task_signals'),
                       ('resolution_signal_id', 'id', 'business_task_signals')),
    'project_candidate': (('cluster_id', 'work_cluster_public_id', 'business'),
                          ('confirmed_project_id', 'project_public_id', 'business'),
                          ('confirmation_signal_id', 'id', 'business_task_signals')),
    'wechat_memory_candidate': (),
}
TRIM_WS = 'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)'


def candidate_column(kind, field):
    return 'public_id' if field == 'id' else field if field in ('title', 'status', 'created_at', 'updated_at') else kind + '_' + field


def candidate_payload_fields(kind):
    return tuple(f for f in CANDIDATE_FIELDS[kind]
                 if f not in ('id', 'title', 'status', 'created_at', 'updated_at'))


def candidate_schema_parts(*, business_action_signals=False):
    columns, checks, indexes = [], [], []
    for kind in CANDIDATE_KINDS:
        payload = candidate_payload_fields(kind)
        for field in payload:
            value = _encoded_value('payload_json', '$.' + field)
            columns.append(f"{candidate_column(kind, field)} {CANDIDATE_TYPES[kind][field]} "
                           f"generated always as (case when record_type='{kind}' then {value} end) virtual")
        valid = [_encoded_value_check('payload_json', '$.' + field) for field in payload]
        valid.extend(f'{candidate_column(kind, field)} is not null'
                     for field in CANDIDATE_NOT_NULL[kind] if field != 'id')
        valid.append("json_remove(payload_json," + ','.join(repr('$.' + f) for f in payload) + ")='{}'")
        if kind == 'attention_item':
            valid.extend((
                f"trim(attention_item_stable_key,{TRIM_WS})<>''",
                "attention_item_category in ('fyi','watch','decision','push')",
                "status in ('active','resolved')",
                *(f"trim({field},{TRIM_WS})<>''" for field in
                  ('title', 'attention_item_why_attention', 'attention_item_current_state',
                   'attention_item_ceo_action')),
                "((status='active' and attention_item_resolution_signal_id is null and attention_item_resolved_at='')"
                " or (status='resolved' and attention_item_resolution_signal_id is not null and "
                f"trim(attention_item_resolved_at,{TRIM_WS})<>''))",
            ))
            indexes.extend((
                "create unique index idx_business_attention_stable_key on business(attention_item_stable_key) where record_type='attention_item'",
                "create index idx_business_attention_list on business(status,attention_item_category,updated_at,public_id) where record_type='attention_item'",
            ))
        elif kind == 'project_candidate':
            valid.extend((
                f"trim(title,{TRIM_WS})<>''", f"trim(project_candidate_reason,{TRIM_WS})<>''",
                "status in ('proposed','confirmed','rejected')",
                "(status='confirmed')=(project_candidate_confirmed_project_id is not null)",
                "(status='confirmed')=(project_candidate_confirmation_signal_id is not null)",
            ))
            indexes.append("create index idx_business_project_candidate_cluster on business(project_candidate_cluster_id,status,public_id) where record_type='project_candidate'")
        else:
            indexes.append("create unique index idx_business_wechat_candidate_identity on business(wechat_memory_candidate_import_run_id,wechat_memory_candidate_statement) where record_type='wechat_memory_candidate'")
        checks.append(f"check(record_type<>'{kind}' or ({' and '.join('(' + x + ')' for x in valid)}))")
        for field, endpoint, parent in CANDIDATE_REFERENCES[kind]:
            if business_action_signals and parent == 'business_task_signals':
                parent, endpoint = 'actions', 'business_signal_public_id'
            checks.append(f"foreign key({candidate_column(kind, field)}) references {parent}({endpoint}) on update no action on delete no action")
    return columns, checks, indexes


def candidate_projection(kind, source_variant='actual', with_rowid=False):
    if kind not in CANDIDATE_KINDS or source_variant not in ('actual', 'fresh'):
        raise ValueError('unsupported Business candidate projection')
    fields = (CANDIDATE_FIELDS if source_variant == 'actual' else CANDIDATE_FRESH_FIELDS)[kind]
    selected = (['public_id as _source_rowid'] if with_rowid else []) + [
        candidate_column(kind, field) + ' as ' + field for field in fields]
    return 'select ' + ','.join(selected) + f" from business where record_type='{kind}'"
