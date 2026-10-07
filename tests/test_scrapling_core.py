#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scrapling 框架模型移植回归（scrapling_core.py）+ 社区爬虫（community_spider.py）。

零联网：全部用假传输层（FetcherSession(transport=...)）驱动，测的是框架行为本身：
  • FetcherSession：stealthy headers / 重试 / 重定向历史 / 编码识别 / gzip / Retry-After
  • Selector：CSS 子集、get_all_text、HTML→Markdown、选择器生成、::text / ::attr
  • Adaptive：元素指纹入库 + 改版后按相似度回捞（auto_match / relocate）
  • Scheduler / AutoThrottle / RobotsTxtManager / DevCache / CheckpointManager
  • Spider + CrawlerEngine：并发、封锁重试、offsite、on_scraped_item、robots 遵从、开发缓存
  • CommunitySpider：49 源抽取规则覆盖 + 自适应回捞 + 统计口径
"""
import gzip
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import community_data as cd        # noqa: E402
import community_spider as csp     # noqa: E402
import scrapling_core as sc        # noqa: E402

PAGE = """<html><head><title>港股社区 · 恒指 26,000</title>
<meta name="description" content="恒指今日收报 26,012 点">
</head><body>
  <div id="feed" class="list hot" data-cid="42">
    <article class="post"><h3 class="title">恒指 26,000 关口压力重重</h3>
      <p>球友认为短线偏空，中期仍看南向资金<b>回流</b>。</p>
      <a href="/p/1">详情</a></article>
    <article class="post"><h3 class="title">南向净买入 62 亿</h3></article>
  </div>
  <script>var x = 1;</script>
  <div style="display:none">隐藏的注入内容</div>
</body></html>"""


def make_transport(pages: dict, calls: list = None, block_urls=(), status=200, raw: bytes = None):
    """假传输层：按 URL 返回页面，并把每次调用记进 calls。"""

    def transport(method, url, headers, timeout, proxy=None, body=None):
        if calls is not None:
            calls.append({'method': method, 'url': url, 'headers': headers, 'timeout': timeout,
                          'proxy': proxy})
        for marker in block_urls:
            if marker in url:
                return 429, 'Too Many Requests', {'Retry-After': '0'}, b'blocked', url
        content = raw if raw is not None else pages.get(url, PAGE).encode('utf-8')
        headers_out = {'Content-Type': 'text/html; charset=utf-8'}
        if raw is not None:
            headers_out = {'Content-Type': 'text/html; charset=gbk'}
        return status, 'OK', headers_out, content, url

    return transport


class TestFetcherSession(unittest.TestCase):
    def test_stealth_headers_and_encoding(self):
        calls = []
        session = sc.FetcherSession(transport=make_transport({}, calls), retries=1)
        response = session.get('https://example.com/x')
        self.assertEqual(response.status, 200)
        self.assertTrue(response.ok)
        user_agent = calls[0]['headers'].get('User-Agent', '')
        self.assertIn('Mozilla/5.0', user_agent)
        self.assertEqual(calls[0]['headers'].get('Sec-Fetch-Mode'), 'navigate')
        self.assertIn('恒指', response.get_all_text())

    def test_gbk_fallback_and_gzip(self):
        raw = '港股通净买入 412 亿'.encode('gbk')
        session = sc.FetcherSession(transport=make_transport({}, raw=raw), retries=1)
        self.assertIn('港股通净买入', session.get('https://example.com/gbk').text)
        payload = gzip.compress('港股通'.encode('utf-8'))

        def gz_transport(method, url, headers, timeout, proxy=None, body=None):
            return 200, 'OK', {'Content-Type': 'text/html', 'Content-Encoding': 'gzip'}, payload, url

        self.assertIn('港股通', sc.FetcherSession(transport=gz_transport, retries=1)
                      .get('https://example.com/gz').text)

    def test_retries_then_success(self):
        calls = []

        def flaky(method, url, headers, timeout, proxy=None, body=None):
            calls.append(url)
            if len(calls) < 3:
                raise OSError('connection reset')
            return 200, 'OK', {'Content-Type': 'text/html'}, b'ok', url

        session = sc.FetcherSession(transport=flaky, retries=3, retry_delay=0, retry_backoff=1)
        self.assertEqual(session.get('https://example.com/flaky').status, 200)
        self.assertEqual(len(calls), 3)
        self.assertEqual(session.stats['retries'], 2)

    def test_retries_exhausted_raises_fetch_error(self):
        session = sc.FetcherSession(transport=make_transport({}, block_urls=('blocked',), status=200),
                                    retries=1) if False else sc.FetcherSession(
            transport=lambda *a, **k: (_ for _ in ()).throw(OSError('boom')), retries=2, retry_delay=0)

        with self.assertRaises(sc.FetchError):
            session.get('https://example.com/dead')

    def test_redirect_history_and_blocked_code(self):
        def redirecting(method, url, headers, timeout, proxy=None, body=None):
            if url.endswith('/start'):
                return 302, 'Found', {'Location': '/final'}, b'', url
            return 200, 'OK', {'Content-Type': 'text/html'}, PAGE.encode('utf-8'), url

        session = sc.FetcherSession(transport=redirecting, retries=1)
        response = session.get('https://example.com/start')
        self.assertEqual(response.url, 'https://example.com/final')
        self.assertEqual([item.status for item in response.history], [302])
        blocked = sc.FetcherSession(transport=make_transport({}, block_urls=('blocked',)), retries=1)
        self.assertIn(blocked.get('https://example.com/blocked').status, sc.BLOCKED_CODES)

    def test_parse_retry_after_seconds_and_http_date(self):
        self.assertEqual(sc.parse_retry_after({'Retry-After': '12'}), 12.0)
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        value = sc.parse_retry_after({'retry-after': future.strftime('%a, %d %b %Y %H:%M:%S GMT')})
        self.assertTrue(25 <= value <= 31, value)
        self.assertIsNone(sc.parse_retry_after({}))

    def test_proxy_rotator_round_robin(self):
        rotator = sc.ProxyRotator(['http://p1', 'http://p2'])
        self.assertEqual([rotator.get_proxy() for _ in range(4)],
                         ['http://p1', 'http://p2', 'http://p1', 'http://p2'])
        calls = []
        session = sc.FetcherSession(transport=make_transport({}, calls), retries=1, proxy_rotator=rotator)
        session.get('https://example.com/a')
        self.assertIn(calls[0]['proxy'], ('http://p1', 'http://p2'))


class TestSelector(unittest.TestCase):
    def setUp(self):
        self.page = sc.Selector(PAGE, url='https://example.com/forum/list?page=1')

    def test_css_subset(self):
        self.assertEqual(self.page.css('title').get().strip(), '港股社区 · 恒指 26,000')
        self.assertEqual(len(self.page.css('article.post')), 2)
        self.assertEqual(len(self.page.css('div#feed > article')), 2)
        self.assertEqual(len(self.page.css('div#feed article:first-of-type')), 1)
        self.assertEqual(self.page.css('a').first.attrib.get('href'), '/p/1')
        self.assertEqual(len(self.page.css('article, h3.title')), 4)
        self.assertIn('恒指 26,000 关口压力重重', self.page.css('::text').get(), '::text 取整元素文本')

    def test_text_and_attr_pseudo(self):
        titles = self.page.css('h3.title::text')
        self.assertEqual(len(titles), 2)
        self.assertTrue(titles.get().startswith('恒指'))
        hrefs = self.page.css('a::attr(href)')
        self.assertEqual(hrefs.get(), '/p/1')

    def test_text_helpers_and_generators(self):
        text = sc.TextHandler('  港股   通 净买入 412 亿  ')
        self.assertEqual(text.clean(), '港股 通 净买入 412 亿')
        self.assertEqual(sc.TextHandler('abc123').re_first(r'\d+'), '123')
        self.assertEqual(self.page.css('div#feed').first.urljoin('/p/2'), 'https://example.com/p/2')
        selector = self.page.css('article.post').first.css('h3').first.generate_css_selector
        self.assertTrue(selector.endswith('h3'))
        self.assertTrue(self.page.css('article.post').first.generate_xpath_selector.startswith('//'))
        found = self.page.find_all('article', class_='post')
        self.assertEqual(len(found), 2)
        self.assertEqual(len(self.page.find_all(tag for tag in ('h3',))), 2)

    def test_markdown_and_noise_stripping(self):
        markdown = sc.Convertor.to_markdown(self.page)
        self.assertIn('### 恒指 26,000 关口压力重重', markdown)
        self.assertIn('**回流**', markdown)
        self.assertIn('[详情](/p/1)', markdown, '相对链接保持相对（与原站一致）')
        cleaned = sc.Convertor.sanitize_for_ai(sc.Convertor.strip_noise_tags(self.page))
        self.assertNotIn('var x = 1', str(cleaned.html_content))
        self.assertNotIn('隐藏的注入内容', str(cleaned.html_content))
        extracted = list(sc.Convertor.extract_content(self.page, 'text', main_content_only=True))
        self.assertTrue(any('恒指' in item for item in extracted))


class TestAdaptive(unittest.TestCase):
    def test_fingerprint_similarity_and_relocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = sc.AdaptiveStorage(os.path.join(tmp, 'adaptive.db'), url='https://example.com/forum')
            try:
                page = sc.Selector(PAGE, url='https://example.com/forum', storage=storage, adaptive=True)
                target = page.css('div#feed h3.title').first
                page.save('x:title', target)
                fingerprint = storage.retrieve('x:title')
                self.assertEqual(fingerprint['tag'], 'h3')
                self.assertIn('parent_name', fingerprint)
                identical = sc.element_similarity(fingerprint, sc.element_to_dict(target._root))
                self.assertEqual(identical, 100.0)
                moved = sc.Selector(PAGE.replace('class="title"', 'class="title renamed"'),
                                    url='https://example.com/forum', storage=storage, adaptive=True)
                relocated = moved.relocate('x:title')
                self.assertIsNotNone(relocated)
                self.assertIn('恒指 26,000', relocated.get())
                # 纯空壳页面回捞不到 → 不硬编内容
                empty = sc.Selector('<html><body><div>nothing</div></body></html>',
                                    url='https://example.com/forum', storage=storage, adaptive=True)
                self.assertIsNone(empty.relocate('x:title', percentage=80))
                self.assertEqual(empty.auto_match('div#feed h3.title', 'x:title').getall(), [])
            finally:
                storage.close()

    def test_adaptive_requires_storage(self):
        with self.assertRaises(ValueError):
            sc.Selector(PAGE, adaptive=True)


class TestSchedulerThrottleAndRobots(unittest.TestCase):
    def test_scheduler_priority_dedup_and_snapshot(self):
        scheduler = sc.Scheduler()
        self.assertTrue(scheduler.enqueue(sc.Request('https://a.com/1', priority=0)))
        self.assertTrue(scheduler.enqueue(sc.Request('https://a.com/2', priority=5)))
        self.assertFalse(scheduler.enqueue(sc.Request('https://a.com/1')), '同 URL 指纹要去重')
        self.assertTrue(scheduler.enqueue(sc.Request('https://a.com/1', dont_filter=True)))
        first = scheduler.dequeue()
        self.assertEqual(first.url, 'https://a.com/2', '高优先级先出队')
        scheduler.complete(first)
        requests, seen = scheduler.snapshot()
        self.assertEqual(len(requests), 2, '完成的那条不再计入待抓快照')
        self.assertEqual(len(seen), 2, '去重后只应有两个不同指纹（dont_filter 复用同一指纹）')
        restored = sc.Scheduler()
        restored.restore(requests, seen)
        self.assertEqual(len(restored), 2)

    def test_autothrottle_math_matches_upstream(self):
        throttle = sc.AutoThrottle(start_delay=5, max_delay=60, target_concurrency=1)
        self.assertEqual(throttle.delay_for('a.com'), 5)
        self.assertEqual(throttle.record('a.com', latency=1.0, ok=True), 3.0)
        self.assertEqual(throttle.record('a.com', latency=4.0, ok=True), 4.0,
                         '延迟变大 → 取 (3+4)/2 与 target=4 的较大者')
        self.assertEqual(throttle.record('a.com', latency=0.1, ok=False), 8.0, '封锁时按 ×2 退避')
        self.assertEqual(throttle.record('a.com', latency=0.1, ok=False, retry_after=42.0), 42.0)
        self.assertEqual(throttle.delay_for('a.com', floor=100), 60.0, '不得超过 max_delay')
        with self.assertRaises(ValueError):
            sc.AutoThrottle(start_delay=10, max_delay=5)

    def test_robots_txt_parsing(self):
        content = ('User-agent: *\nDisallow: /private\nAllow: /private/public\n'
                   'Crawl-delay: 2\nRequest-rate: 3/10\n')
        robots = sc.RobotsTxtManager(lambda url: sc.Response(
            url=url, content=content, status=200, reason='OK', cookies={}, headers={}, request_headers={}))
        self.assertFalse(robots.can_fetch('https://x.com/private/a'))
        self.assertTrue(robots.can_fetch('https://x.com/private/public/a'), 'Allow 更长路径优先')
        self.assertTrue(robots.can_fetch('https://x.com/open'))
        self.assertEqual(robots.crawl_delay('https://x.com/open'), 2.0)
        self.assertEqual(robots.request_rate('https://x.com/open'), (3, 10.0))
        # robots.txt 抓不到时按「不阻断」处理
        robots_fail = sc.RobotsTxtManager(lambda url: (_ for _ in ()).throw(OSError('nope')))
        self.assertTrue(robots_fail.can_fetch('https://x.com/private/a'))


class TestCacheCheckpointAndEngine(unittest.TestCase):
    def test_dev_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = sc.DevCache(tmp)
            response = sc.Response(url='https://x.com', content='<p>缓存</p>', status=200, reason='OK',
                                   cookies={}, headers={}, request_headers={})
            cache.put('abc', response)
            hit = cache.get('abc')
            self.assertEqual(hit.status, 200)
            self.assertIn('缓存', hit.get_all_text())
            self.assertTrue(hit.meta.get('dev_cache'))
            cache.clear()
            self.assertIsNone(cache.get('abc'))

    def test_checkpoint_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = sc.CheckpointManager(tmp, interval=10)
            self.assertFalse(manager.has_checkpoint())
            request = sc.Request('https://x.com/a', priority=3, meta={'k': 'v'})
            manager.save([request], {request.update_fingerprint()})
            self.assertTrue(manager.has_checkpoint())
            restored, seen = manager.load()
            self.assertEqual(restored[0].url, 'https://x.com/a')
            self.assertEqual(restored[0].priority, 3)
            self.assertEqual(restored[0].meta, {'k': 'v'})
            self.assertEqual(len(seen), 1)
            manager.cleanup()
            self.assertFalse(manager.has_checkpoint())

    def test_engine_offsite_blocked_retry_and_hooks(self):
        calls = []
        pages = {'https://a.com/1': '<a href="https://b.com/2">外链</a><h3>标题</h3>'}

        def transport(method, url, headers, timeout, proxy=None, body=None):
            calls.append(url)
            if '/blocked' in url:
                return 403, 'Forbidden', {}, b'no', url
            return 200, 'OK', {'Content-Type': 'text/html'}, pages.get(url, '<h3>子页</h3>').encode(), url

        dropped = []

        class DemoSpider(sc.Spider):
            name = 'demo-engine'
            start_urls = ['https://a.com/1', 'https://a.com/blocked']
            allowed_domains = {'a.com'}
            concurrent_requests = 2
            max_blocked_retries = 1
            autothrottle_enabled = False

            def parse(self, response):
                if 'blocked' in response.url:
                    return
                for link in response.css('a'):
                    yield sc.Request(response.urljoin(link.attrib['href']))
                yield {'url': response.url, 'title': response.css('h3').get()}

            def on_scraped_item(self, item):
                if item['url'].endswith('/1'):
                    dropped.append(item['url'])
                    return None
                return item

        spider = DemoSpider(session=sc.FetcherSession(transport=transport, retries=1))
        response = TestCacheCheckpointAndEngine._result(spider)
        self.assertEqual(response.stats.blocked_requests_count, 2, '403 首次 + 1 次重试都计入封锁')
        self.assertGreaterEqual(response.stats.requests_count, 2)
        self.assertEqual(response.stats.offsite_requests_count, 1, 'b.com 不在 allowed_domains：计数但不入队')
        self.assertNotIn('https://b.com/2', calls, '站外请求不得真的抓取')
        self.assertEqual(dropped, ['https://a.com/1'], 'on_scraped_item 可以丢弃条目')
        self.assertGreaterEqual(response.stats.items_scraped, 1)
        self.assertTrue(response.completed)
        self.assertIn('status_200', response.stats.response_status_count)

    @staticmethod
    def _result(spider):
        return spider.crawl()

    def test_robots_obey_skips_disallowed(self):
        calls = []
        robots_txt = 'User-agent: *\nDisallow: /blocked\n'

        def transport(method, url, headers, timeout, proxy=None, body=None):
            calls.append(url)
            if url.endswith('/robots.txt'):
                return 200, 'OK', {'Content-Type': 'text/plain'}, robots_txt.encode(), url
            return 200, 'OK', {'Content-Type': 'text/html'}, b'<h3>hi</h3>', url

        class RobotsSpider(sc.Spider):
            name = 'demo-robots'
            start_urls = ['https://a.com/ok', 'https://a.com/blocked']
            robots_txt_obey = True
            concurrent_requests = 1
            autothrottle_enabled = False

            def parse(self, response):
                yield {'url': response.url}

        result = RobotsSpider(session=sc.FetcherSession(transport=transport, retries=1)).crawl()
        self.assertEqual(result.stats.robots_disallowed_count, 1)
        self.assertEqual([item['url'] for item in result.items], ['https://a.com/ok'])
        self.assertTrue(any(url.endswith('/robots.txt') for url in calls), '必须先取 robots.txt')

    def test_development_mode_serves_from_cache(self):
        calls = []

        class CachedSpider(sc.Spider):
            name = 'demo-cache'
            start_urls = ['https://a.com/x']
            development_mode = True
            autothrottle_enabled = False

            def parse(self, response):
                yield {'url': response.url}

        with tempfile.TemporaryDirectory() as tmp:
            spider = CachedSpider(session=sc.FetcherSession(transport=make_transport({}, calls), retries=1))
            spider.development_cache_dir = os.path.join(tmp, 'cache')
            first = spider.crawl()
            second = spider.crawl()
        self.assertEqual(len(first.items), 1)
        self.assertEqual(len(second.items), 1)
        self.assertEqual(len([call for call in calls if call['url'].endswith('/x')]), 1,
                         '第二次应命中开发缓存')
        self.assertEqual(second.stats.cache_hits, 1)


class TestCommunitySpider(unittest.TestCase):
    def test_site_rules_cover_the_catalog(self):
        missing = [c['key'] for c in cd.COMMUNITIES if c['key'] not in csp.SITE_RULES]
        self.assertEqual(missing, [])
        self.assertEqual(len(cd.COMMUNITIES), 49)

    def test_extracts_snippet_and_falls_back_to_adaptive(self):
        communities = [
            {'id': '01', 'key': 'FUTU', 'name': '富途牛牛社区', 'ctype': '中文行情社区',
             'url': 'https://a.com/forum'},
            {'id': '35', 'key': 'JISILU', 'name': '集思录', 'ctype': '中文低风险投资社区',
             'url': 'https://b.com/forum'},
            {'id': '49', 'key': 'AASTOCKS', 'name': '阿斯达克财经 · 讨论区', 'ctype': '香港本地财经社区',
             'url': 'https://c.com/forum'},
        ]
        rules_page = ('<html><head><title>港股社区</title></head><body>'
                      '<h1>恒指 26,000 关口压力重重 · 南向净买入 62 亿</h1></body></html>')
        generic_page = ('<html><head><title>讨论区</title></head><body>'
                        '<div class="copyright">版权声明</div>'
                        '<article><p>港股今日成交放大，恒指收报 26,012 点，恒生科技同步走强。</p></article>'
                        '</body></html>')
        pages = {'https://a.com/forum': rules_page,
                 'https://b.com/forum': generic_page,
                 'https://c.com/forum': generic_page}
        with tempfile.TemporaryDirectory() as tmp:
            session = sc.FetcherSession(transport=make_transport(pages), retries=1)
            spider = csp.CommunitySpider(communities, storage_file=os.path.join(tmp, 'a.db'),
                                         session=session, verbose=False)
            spider.autothrottle_enabled = False
            result = spider.crawl()
            results = dict(spider.results)
        self.assertEqual(len(result.items), 3)
        self.assertTrue(results['FUTU']['ok'])
        self.assertIn('恒指 26,000', results['FUTU']['snippet'])
        self.assertIn('恒指收报 26,012', results['JISILU']['snippet'], '通用阶梯要能抓正文')
        self.assertEqual(results['AASTOCKS']['title'], '讨论区')
        self.assertNotIn('版权声明', results['JISILU']['snippet'], '噪音块要被打分器淘汰')

    def test_fetch_live_snippets_reports_stats_and_backend(self):
        communities = [{'id': '01', 'key': 'FUTU', 'name': '富途牛牛社区', 'ctype': '中文行情社区',
                        'url': 'https://a.com/forum'}]
        with tempfile.TemporaryDirectory() as tmp:
            session = sc.FetcherSession(transport=make_transport(
                {'https://a.com/forum': '<html><body><h1>恒指收报 26,012 点</h1></body></html>'}), retries=1)
            results, stats = csp.fetch_live_snippets(communities, session=session,
                                                     storage_file=os.path.join(tmp, 'a.db'),
                                                     verbose=False)
        self.assertEqual(stats['backend'], 'injected')
        self.assertEqual(stats['sources'], 1)
        self.assertEqual(stats['ok'], 1)
        self.assertEqual(stats['failed'], [])
        self.assertTrue(stats['engine'].startswith('scrapling-core'))
        self.assertIn('恒指收报 26,012', results['FUTU']['snippet'])

    def test_dom_change_is_recovered_by_adaptive_fingerprint(self):
        """第一次抓取存指纹；站点改版（类名/结构变）后仍能回捞同一块热评。"""
        communities = [{'id': '01', 'key': 'FUTU', 'name': '富途牛牛社区', 'ctype': '中文行情社区',
                        'url': 'https://a.com/forum'}]
        before = ('<html><body><main><article class="post"><h3 class="title">'
                  '恒指 26,000 关口压力重重</h3></article></main></body></html>')
        after = ('<html><body><div class="brand-new-wrap"><div class="totally-different">'
                 '恒指 26,000 关口压力重重</div></div></body></html>')
        with tempfile.TemporaryDirectory() as tmp:
            storage_file = os.path.join(tmp, 'a.db')
            csp.fetch_live_snippets(communities, session=sc.FetcherSession(
                transport=make_transport({'https://a.com/forum': before}), retries=1),
                storage_file=storage_file, verbose=False)
            _, stats = csp.fetch_live_snippets(communities, session=sc.FetcherSession(
                transport=make_transport({'https://a.com/forum': after}), retries=1),
                storage_file=storage_file, verbose=False)
        self.assertEqual(stats['adaptive_hits'], 1, '改版后应走指纹回捞')

    def test_score_fragment_prefers_topic_text(self):
        self.assertLess(csp.score_fragment('登录 注册 客服'), 0)
        self.assertGreater(csp.score_fragment('恒指今日收报 26,012 点，南向净买入 62 亿'),
                           csp.score_fragment('今天的天气不错，适合出去走走看看风景')) 


if __name__ == '__main__':
    unittest.main()
