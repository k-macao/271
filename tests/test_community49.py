#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""49 大社区回归（14 原有 + 20 前次新增 + 15 本次扩容）+ 跨域组合口径 + Scrapling 抓取框架接入。

本次三项需求的回归防线：
  1. 接入 GitHub 上的 Scrapling，分析并使用其代码框架模型（scrapling_core.py / community_spider.py）；
  2. 社区模块改由该框架抓取（Spider + Scheduler + AutoThrottle + 自适应指纹回捞 + 封锁重试）；
  3. 社区源头从 34 扩到 **49** —— 目录、模板、量化指标、网页注入、微信推送五处源数一致，
     并且微信单页仍要落在 95,000 字符推送门禁之内。

零联网：全部用 offline_dataset()/--demo 的当次模板数据与假传输层。
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_site as bs            # noqa: E402
import community_data as cd        # noqa: E402
import community_spider as cs      # noqa: E402
import quant_pair                  # noqa: E402
import scrapling_core as sc        # noqa: E402
import wechat_push as wp           # noqa: E402

NOW = datetime(2026, 10, 3, 3, 0, 0, tzinfo=timezone.utc)

MARKET = {'fetch_date': '2026-10-03', 'quotes': {
    'HSI': {'name': '恒生指数', 'last': 26000, 'prev_close': 25800, 'pct': 0.78, 'as_of': '2026-10-03'},
    'HSTECH': {'name': '恒生科技指数', 'last': 5200, 'prev_close': 5150, 'pct': 0.97, 'as_of': '2026-10-03'},
    'HSCE': {'name': '恒生中国企业指数', 'last': 9000, 'prev_close': 8940, 'pct': 0.67, 'as_of': '2026-10-03'},
    'SPX': {'name': '标普 500', 'last': 6000, 'prev_close': 5990, 'pct': 0.17, 'as_of': '2026-10-02'},
    'NDQ': {'name': '纳斯达克', 'last': 21000, 'prev_close': 20900, 'pct': 0.48, 'as_of': '2026-10-02'},
    'DJI': {'name': '道琼斯', 'last': 43000, 'prev_close': 42900, 'pct': 0.23, 'as_of': '2026-10-02'},
    'GOLD': {'name': '现货黄金', 'last': 2650, 'prev_close': 2640, 'pct': 0.38, 'as_of': '2026-10-02'},
    'WTI': {'name': 'WTI 原油', 'last': 92, 'prev_close': 90, 'pct': 2.2, 'as_of': '2026-10-02'},
    'BRENT': {'name': '布伦特原油', 'last': 95, 'prev_close': 93, 'pct': 2.1, 'as_of': '2026-10-02'},
    'USDCNH': {'name': '美元/离岸人民币', 'last': 6.71, 'prev_close': 6.72, 'pct': -0.15, 'as_of': '2026-10-03'},
    'USDCNY': {'name': '美元/在岸人民币', 'last': 6.70, 'prev_close': 6.71, 'pct': -0.15, 'as_of': '2026-10-03'},
}}


class TestCommunityCatalog(unittest.TestCase):
    def test_has_49_sources_with_unique_ids_and_keys(self):
        comms = cd.COMMUNITIES
        self.assertEqual(len(comms), 49, '14 原有 + 20 前次新增 + 15 本次扩容 = 49 源')
        self.assertEqual([c['id'] for c in comms], [f'{i:02d}' for i in range(1, 50)])
        keys = [c['key'] for c in comms]
        self.assertEqual(len(set(keys)), 49, '社区 key 不得重复')
        self.assertEqual(len({c['name'] for c in comms}), 49, '社区名不得重复')
        original = keys[:14]
        self.assertEqual(original[0], 'FUTU')
        self.assertEqual(original[-1], 'FINTWIT')

    def test_15_new_sources_cover_more_languages_and_types(self):
        new = cd.COMMUNITIES[34:]
        self.assertEqual(len(new), 15, '本次扩容 15 个社区')
        self.assertEqual([c['key'] for c in new][0], 'JISILU')
        self.assertEqual([c['key'] for c in new][-1], 'AASTOCKS')
        english_names = [c for c in new if re.search(r'[A-Za-z]{4,}', c['name'])]
        self.assertGreaterEqual(len(english_names), 4, '扩容里要有足量英文社区')
        types = {c.get('ctype') for c in cd.COMMUNITIES}
        self.assertGreaterEqual(len(types), 30, f'社区类型要足够多样，实际 {len(types)} 类')
        for must in ('中文低风险投资社区', '中文短视频社区', '英文量化社区', '数字资产论坛',
                     '英文基金研究社区', '日文社区', '韩文社区', '德文社区', '香港本地财经社区'):
            self.assertIn(must, types, f'缺少「{must}」这类社区')
        for c in cd.COMMUNITIES:
            self.assertTrue(c.get('ctype'), f"{c['key']} 缺 ctype（社区类型）")
            self.assertTrue(c['url'].startswith('http'), f"{c['key']} 缺可抓取 URL")
            self.assertIn(c['verdict_class'], ('bull', 'bear', 'neutral', 'mixed'))

    def test_every_source_renders_quote_verdict_and_quant_with_today(self):
        hsi = cd.fmt_hsi(MARKET)
        quotes = set()
        for c in cd.COMMUNITIES:
            q = cd.generate_dynamic_quote(c, hsi, '2026-10-03', '10 月 3 日', live_snippet='')
            v = cd.generate_verdict(c, hsi, '10 月 3 日')
            m = cd.generate_quant_metrics(c, hsi, '', 'fallback', '2026-10-03')
            self.assertIn('10 月 3 日', q, f"{c['key']} 的正文必须带当次日期")
            self.assertTrue(v.strip(), f"{c['key']} 缺战术研判")
            self.assertTrue(m['event']['label'] and m['relevance']['score'], f"{c['key']} 缺量化指标")
            quotes.add(q)
        self.assertEqual(len(quotes), 49, '49 源正文不得互相复制粘贴')

    def test_offline_dataset_matches_live_structure(self):
        d = cd.offline_dataset(MARKET, now=NOW)
        self.assertEqual(d['mode'], 'fallback')
        self.assertEqual(d['fetch_date'], '2026-10-03')
        self.assertEqual(len(d['communities']), 49)
        self.assertEqual(d['summary'], {'ok': 49, 'total': 49, 'failed': []})
        self.assertTrue(d['fetch_engine']['engine'].startswith('scrapling-core'))
        for c in d['communities']:
            for field in ('id', 'key', 'name', 'icon', 'ctype', 'url', 'verdict_label',
                          'verdict_class', 'quote', 'verdict', 'quant', 'meta', 'fetch_date'):
                self.assertIn(field, c, f"{c['key']} 缺字段 {field}")
            self.assertIn('最新读取 2026-10-03', c['meta'], '推送前频道核对依赖这条标记')
            self.assertIn('10 月 3 日', c['quote'], '兜底正文也必须带当次日期')


class TestScraplingEngineWiring(unittest.TestCase):
    """抓取层（Scrapling 框架模型）真的接进来了，且覆盖 49 个源。"""

    def test_spider_is_the_fetch_path_and_covers_every_source(self):
        self.assertTrue(issubclass(cs.CommunitySpider, sc.Spider),
                        'CommunitySpider 必须继承移植后的 Spider 基类')
        missing = [c['key'] for c in cd.COMMUNITIES if c['key'] not in cs.SITE_RULES]
        self.assertEqual(missing, [], f'每个社区源都要有抽取规则，缺: {missing}')
        self.assertTrue(cd.ADAPTIVE_DB_DEFAULT.endswith('community_adaptive.db'))
        self.assertIn('.scrapling', cd.ADAPTIVE_DB_DEFAULT, '自适应指纹库要落在忽略目录里')

    def test_community_data_main_uses_the_spider(self):
        """`community_data.py` 的 live 分支必须走 community_spider，而不是裸 urllib。"""
        captured = {}

        def fake_fetch(communities, **kwargs):
            captured['sources'] = list(communities)
            captured['kwargs'] = kwargs
            results = {c['key']: {'key': c['key'], 'name': c['name'], 'status': 200, 'ok': True,
                                  'snippet': f"现场片段 {c['name']} · 恒指 26,012 点",
                                  'selector': 'h3', 'adaptive': False, 'bytes': 1024}
                       for c in communities}
            stats = {'engine': sc.ENGINE_NAME, 'backend': 'injected', 'sources': len(communities),
                     'ok': len(communities), 'failed': [], 'adaptive_hits': 0,
                     'blocked_requests_count': 0, 'elapsed': 0.1}
            return results, stats

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'community_data.json')
            market = os.path.join(tmp, 'market_data.json')
            with open(market, 'w', encoding='utf-8') as handle:
                json.dump(MARKET, handle, ensure_ascii=False)
            original = cs.fetch_live_snippets
            cs.fetch_live_snippets = fake_fetch
            argv = sys.argv[:]
            sys.argv = ['community_data.py', '--json', out, '--market-data', market]
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    cd.main()
            finally:
                cs.fetch_live_snippets = original
                sys.argv = argv
            with open(out, encoding='utf-8') as handle:
                data = json.load(handle)
        self.assertEqual(len(captured['sources']), 49, '抓取入口必须收到 49 个源')
        self.assertEqual(len(data['communities']), 49)
        self.assertEqual(data['summary']['ok'], 49)
        self.assertTrue(all(c['source'] == 'live' for c in data['communities']))
        self.assertIn('恒指 26,012', data['communities'][0]['quote'], '活数据要进正文佐证')
        self.assertEqual(data['fetch_engine']['backend'], 'injected')


class TestCrossDomainPairs(unittest.TestCase):
    def test_pair_catalog_and_recommendations_are_always_cross_domain(self):
        self.assertEqual(quant_pair.cross_domain_catalog_errors(), [])
        quotes = {'quotes': {k: {'name': v['name'], 'pct': v['pct']} for k, v in MARKET['quotes'].items()}}
        for c in cd.COMMUNITIES:
            rec = quant_pair.recommend(f"{c['name']} 讨论", quotes, hint=c['key'])
            self.assertNotEqual(rec['domain_a'], rec['domain_b'],
                                f"{c['key']} 给出的是同域组合：{rec['domain_pair']}")
            self.assertTrue(rec['cross_domain'])
            self.assertNotEqual(rec['leg_a']['key'], rec['leg_b']['key'])

    def test_every_community_card_in_the_web_page_has_a_cross_domain_pair(self):
        d = cd.offline_dataset(MARKET, now=NOW)
        html = bs.build_community_html(d['communities'], market=MARKET)
        self.assertEqual(html.count('<article class="pub-card"'), 49)
        self.assertEqual(html.count('class="ai-quant"'), 49)
        pairs = re.findall(r'data-domain-pair="([^"]+)"', html)
        self.assertEqual(len(pairs), 49)
        for p in pairs:
            a, b = [x.strip() for x in p.split('×')]
            self.assertTrue(a and b, f'域对标签不完整: {p}')
            self.assertNotEqual(a, b, f'同域组合不允许出现在卡片里: {p}')
        self.assertEqual(html.count('跨域组合：'), 49)

    def test_report_template_labels_are_dynamic_not_hardcoded_14(self):
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as f:
            tpl = f.read()
        for token in ('{{COMMUNITY_TOTAL}}', '{{COMMUNITY_TYPE_TOTAL}}',
                      '{{CF_BULL}}', '{{CF_BEAR}}', '{{CF_NEUTRAL}}', '{{CF_MIXED}}'):
            self.assertIn(token, tpl, f'模板应使用动态口径 token: {token}')
        self.assertNotIn('全部 14 平台', tpl, '扩展后不得再写死「全部 14 平台」')
        self.assertNotIn('14 SOURCE FEEDS', tpl)
        self.assertNotIn('含核心量化指标 · AI 量化配对）', tpl)

    def test_build_site_tokens_follow_the_dataset(self):
        d = cd.offline_dataset(MARKET, now=NOW)
        tokens = bs.build_tokens(MARKET, NOW, community_data=d)
        self.assertEqual(tokens['{{COMMUNITY_TOTAL}}'], '49')
        self.assertEqual(int(tokens['{{CF_BULL}}']) + int(tokens['{{CF_BEAR}}'])
                         + int(tokens['{{CF_NEUTRAL}}']) + int(tokens['{{CF_MIXED}}']), 49)
        # 49 个「最新读取」日期占位符逐个都要在（模板里静态卡片只用前 14 个，占位符必须齐）
        for index in range(1, 50):
            self.assertEqual(tokens[f'{{{{CD_{index:02d}}}}}'], '2026-10-03')
        # 无社区数据时退回模板静态卡片的 14 源口径，标题不会显示成 49
        empty = bs.build_tokens(MARKET, NOW, community_data={})
        self.assertEqual(empty['{{COMMUNITY_TOTAL}}'], '14')


class TestWechat49Sources(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, payload):
        path = os.path.join(self._tmp.name, name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        return path

    def _render(self, communities):
        env = {
            'MARKET_DATA': self._write('market_data.json', MARKET),
            'COMMUNITY_DATA': self._write('community_data.json', communities),
            'SENTIMENT_DATA': os.path.join(self._tmp.name, 'missing.json'),
            'MACRO_DATA': os.path.join(self._tmp.name, 'missing.json'),
            'MACRO_AUTO_FETCH': '0',
            'FORECAST_HISTORY': os.path.join(self._tmp.name, 'forecast_history.json'),
        }
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return html

    def test_all_49_sources_are_pushed_with_read_markers(self):
        d = cd.offline_dataset(MARKET, now=NOW)
        html = self._render(d)
        markers = re.findall(r'综合站内[^<]*?最新读取\s+(20\d{2}-\d{2}-\d{2})', html)
        self.assertEqual(len(markers), 49, '49 源都要有「最新读取」标记供推送前核对')
        self.assertEqual(set(markers), {'2026-10-03'})
        self.assertIn('49 大平台', html)
        # 每一张社区卡都要挂跨域两标的（完整版是「◆ AI 量化」块，预算降级到名录版时
        # 是「跨域配对：」一行 —— 两种形态都必须一源一份，一源不删）
        per_source_blocks = len(re.findall(r'◆ AI 量化|跨域配对：', html))
        self.assertGreaterEqual(per_source_blocks, 49)
        self.assertIn('跨域', html)
        self.assertLess(len(html), wp.CONTENT_SAFE_LIMIT, '49 源仍须落在推送字符门禁之内')
        for key in ('ZHIHU', 'PTT', 'FTALPHA', 'JISILU', 'NAVER', 'AASTOCKS'):
            src = next(c for c in d['communities'] if c['key'] == key)
            self.assertIn(src['name'], html, f'{key} 必须出现在推送正文里')

    def test_budget_ladder_keeps_every_source_and_keeps_forecast(self):
        """49 源把单页撑满时按预算降级渲染，但**一源不删**，且 04 栏仍有落点。"""
        d = cd.offline_dataset(MARKET, now=NOW)
        html = self._render(d)
        self.assertNotIn('<!--COMMUNITY-SLOT-->', html, '占位槽必须被填充')
        self.assertNotIn('<!--FORECAST-SLOT-->', html)
        for c in d['communities']:
            self.assertIn(c['name'], html, f"{c['key']} 在预算降级时被整条删掉了")
        self.assertIn('04 / AI 预测', html)

    def test_fewer_sources_still_render_and_use_the_same_structure(self):
        d = cd.offline_dataset(MARKET, now=NOW)
        small = dict(d, communities=d['communities'][:3], summary={'ok': 3, 'total': 3, 'failed': []})
        html = self._render(small)
        self.assertEqual(len(re.findall(r'最新读取\s+20\d{2}-\d{2}-\d{2}', html)), 3)
        self.assertIn('3 个境内外核心社区信号', html)
        self.assertNotIn('49 大平台', html, '源数文案必须跟着当次数据走')

    def test_expected_channel_count_matches_the_catalog(self):
        self.assertEqual(wp.EXPECTED_CHANNEL_COUNT, len(cd.COMMUNITIES))
        self.assertEqual(wp.EXPECTED_CHANNEL_COUNT, 49)
        # 逐频道核对：49 条标记齐全时放行，缺项时严格模式退出
        parts = [('t', '\n'.join(f'综合站内 10 条 · 最新读取 2026-10-03' for _ in range(49)))]
        wp.assert_fetch_dates_are_today(parts, NOW, strict=False)
        short = [('t', '综合站内 10 条 · 最新读取 2026-10-03')]
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                wp.assert_fetch_dates_are_today(short, NOW, strict=True)


if __name__ == '__main__':
    unittest.main()
