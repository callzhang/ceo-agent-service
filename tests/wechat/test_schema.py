from app.wechat import schema


MSGSOURCE = (
    b"<msgsource>\n"
    b"\t<atuserlist><![CDATA[wxid_aaa,wxid_bbb,derek840121]]></atuserlist>\n"
    b"\t<membercount>480</membercount>\n"
    b"</msgsource>\n"
)


def test_parse_mentions_extracts_wxids_from_atuserlist():
    assert schema.parse_mentions(MSGSOURCE, 0) == ["wxid_aaa", "wxid_bbb", "derek840121"]


def test_parse_mentions_single_wxid_and_no_cdata():
    assert schema.parse_mentions(b"<msgsource><atuserlist>wxid_solo</atuserlist></msgsource>", 0) == ["wxid_solo"]


def test_parse_mentions_absent_returns_empty():
    assert schema.parse_mentions(b"<msgsource><pua>1</pua></msgsource>", 0) == []
    assert schema.parse_mentions(None, 0) == []
    assert schema.parse_mentions(b"", 0) == []


def test_parse_mentions_supports_self_membership_check():
    ids = schema.parse_mentions(MSGSOURCE, 0)
    assert "derek840121" in ids          # @self detected by wxid
    assert "wxid_not_me" not in ids


def test_parse_mentions_decompresses_zstd_source():
    # emulate WCDB_CT_source==4 by compressing the msgsource with libzstd
    import ctypes
    lib = schema._ZSTD
    if lib is None:
        return  # zstd not available in this env; skip silently
    lib.ZSTD_compressBound.restype = ctypes.c_size_t
    lib.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
    lib.ZSTD_compress.restype = ctypes.c_size_t
    lib.ZSTD_compress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    cap = lib.ZSTD_compressBound(len(MSGSOURCE))
    out = ctypes.create_string_buffer(cap)
    n = lib.ZSTD_compress(out, cap, MSGSOURCE, len(MSGSOURCE), 3)
    compressed = out.raw[:n]
    assert compressed[:4] == schema.ZSTD_MAGIC
    assert schema.parse_mentions(compressed, 4) == ["wxid_aaa", "wxid_bbb", "derek840121"]


def test_shared_article_is_text_eligible_after_decoding():
    content = b"<appmsg><title><![CDATA[An article]]></title><url><![CDATA[https://example.com/a]]></url></appmsg>"
    text = schema.decode_message(content, 0, 49)
    assert text == "[链接]《An article》 https://example.com/a"
    assert schema.kind_for(49, text) == "text"


def test_image_remains_non_text_without_media_input():
    assert schema.kind_for(3, "") == "unknown"


VOICE_XML = (
    b'<msg><voicemsg endflag="1" voiceformat="4" voicelength="27280" length="21060" '
    b'fromusername="wxid_friend" /></msg>'
)


def test_voice_is_its_own_kind_with_a_placeholder_that_is_never_empty():
    text = schema.decode_message(VOICE_XML, 0, 34)
    assert schema.kind_for(34, text) == "voice"
    assert text.startswith("[语音27秒")
    assert "save_wechat_voice" in text


def test_group_voice_with_sender_prefix_and_missing_length_still_has_a_placeholder():
    text = schema.decode_message(b"wxid_a:\n<msg><voicemsg /></msg>", 0, 34)
    assert schema.kind_for(34, text) == "voice"
    assert text.startswith("[语音·未转写")

