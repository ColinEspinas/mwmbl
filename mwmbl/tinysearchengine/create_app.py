import re
from logging import getLogger
from operator import itemgetter
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from mwmbl.tinysearchengine.completer import Completer
from mwmbl.tinysearchengine.hn_top_domains_filtered import DOMAINS
from mwmbl.tinysearchengine.indexer import TinyIndex, Document

logger = getLogger(__name__)


SCORE_THRESHOLD = 0.25


def create(tiny_index: TinyIndex, completer: Completer):
    app = FastAPI()
    
    # Allow CORS requests from any site
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_headers=["*"],
    )

    @app.get("/search")
    def search(s: str):
        results, terms = get_results(s)

        is_complete = s.endswith(' ')
        pattern = get_query_regex(terms, is_complete)
        formatted_results = []
        for result in results:
            formatted_result = {}
            for content_type, content in [('title', result.title), ('extract', result.extract)]:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                all_spans = [0] + sum((list(m.span()) for m in matches), []) + [len(content)]
                content_result = []
                for i in range(len(all_spans) - 1):
                    is_bold = i % 2 == 1
                    start = all_spans[i]
                    end = all_spans[i + 1]
                    content_result.append({'value': content[start:end], 'is_bold': is_bold})
                formatted_result[content_type] = content_result
            formatted_result['url'] = result.url
            formatted_results.append(formatted_result)

        logger.info("Return results: %r", formatted_results)
        return formatted_results

    def get_query_regex(terms, is_complete):
        if not terms:
            return ''

        if is_complete:
            term_patterns = [rf'\b{term}\b' for term in terms]
        else:
            term_patterns = [rf'\b{term}\b' for term in terms[:-1]] + [rf'\b{terms[-1]}']
        pattern = '|'.join(term_patterns)
        return pattern

    def score_result(terms, result: Document, is_complete: bool):
        domain = urlparse(result.url).netloc
        domain_score = DOMAINS.get(domain, 0.0)

        result_string = f"{result.title.strip()} {result.extract.strip()}"
        query_regex = get_query_regex(terms, is_complete)
        matches = list(re.finditer(query_regex, result_string, flags=re.IGNORECASE))
        match_strings = {x.group(0).lower() for x in matches}
        match_length = sum(len(x) for x in match_strings)

        last_match_char = 1
        seen_matches = set()
        for match in matches:
            value = match.group(0).lower()
            if value not in seen_matches:
                last_match_char = match.span()[1]
                seen_matches.add(value)

        total_possible_match_length = sum(len(x) for x in terms)
        score = 0.1*domain_score + 0.9*(match_length + 1./last_match_char) / (total_possible_match_length + 1)
        return score

    def order_results(terms: list[str], results: list[Document], is_complete: bool):
        results_and_scores = [(score_result(terms, result, is_complete), result) for result in results]
        ordered_results = sorted(results_and_scores, key=itemgetter(0), reverse=True)
        filtered_results = [result for score, result in ordered_results if score > SCORE_THRESHOLD]
        return filtered_results

    @app.get("/complete")
    def complete(q: str):
        ordered_results, terms = get_results(q)
        results = [item.title.replace("\n", "") + ' — ' +
                   item.url.replace("\n", "") for item in ordered_results]
        if len(results) == 0:
            return []
        return [q, results]

    def get_results(q):
        terms = [x.lower() for x in q.replace('.', ' ').split()]
        is_complete = q.endswith(' ')
        if len(terms) > 0 and not is_complete:
            retrieval_terms = terms[:-1] + completer.complete(terms[-1])
        else:
            retrieval_terms = terms

        pages = []
        seen_items = set()
        for term in retrieval_terms:
            items = tiny_index.retrieve(term)
            if items is not None:
                for item in items:
                    if term in item.title.lower() or term in item.extract.lower():
                        if item.title not in seen_items:
                            pages.append(item)
                            seen_items.add(item.title)

        ordered_results = order_results(terms, pages, is_complete)
        return ordered_results, terms
    return app
