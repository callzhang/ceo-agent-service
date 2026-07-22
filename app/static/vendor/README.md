# Vendored audit-console assets

These files are served only from the loopback audit application. Keeping exact
copies in the repository prevents a remote script from executing in the audit
origin and reading its mutation token.

| Asset | Version/source | SHA-256 |
| --- | --- | --- |
| `echarts-5.6.0/echarts.min.js` | npm `echarts@5.6.0` | `bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1` |
| `tabulator-6.4.0/tabulator.min.js` | npm `tabulator-tables@6.4.0` | `86df9b98a7cde1098d8cbc0f1916b6989971507984299bc0b4d289a63ed520a0` |
| `tabulator-6.4.0/tabulator.min.css` | npm `tabulator-tables@6.4.0` | `93ab046ce80d8c1933b06b30d530b5835796047aff2e057a1ec458287ba5515b` |
| `dingtalk-jsapi-3.0.25/dingtalk.open.js` | DingTalk official CDN, `https://g.alicdn.com/dingding/dingtalk-jsapi/3.0.25/dingtalk.open.js` | `4a3b5fa97e41f489ef77a18a2c788f0895cdc45cfdc23e46ebe523f29d304524` |

ECharts is Apache-2.0 licensed; its `LICENSE`, `NOTICE`, and the required
BSD-3-Clause `LICENSE-d3` for embedded d3-derived code are included.
Tabulator is MIT licensed; its `LICENSE` is included. `dingtalk-jsapi` declares
the MIT license in its npm package metadata; a copy is included beside the
vendored browser bundle. That bundle also embeds its declared
`promise-polyfill@^7.1.0` dependency; `LICENSE-promise-polyfill` preserves the
MIT notice from the verified `promise-polyfill@7.1.0` package. The built bundle
also contains `lodash.clonedeep@4.5.0`, `lodash.assign@4.2.0`,
`timers-browserify@2.0.12`, `setimmediate@1.0.5`, and the
`process@0.11.10` browser shim; their verified notices are preserved in
`LICENSE-lodash`, `LICENSE-timers-browserify`, `LICENSE-setimmediate`, and
`LICENSE-process`.
