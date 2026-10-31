"""
"How to use this page" inside the admin.

The admin guides in docs/admin-guide/<guide>.md (written for the client's staff,
not developers) are rendered as admin pages at /admin/help/<guide>/, so staff never
need GitHub. The Markdown file stays the single source: the docs and the admin show
the same text.

Admin pages get a "How to use this page" button with HelpLinkMixin:

    class FoodAdmin(HelpLinkMixin, ModelAdmin):
        help_guide, help_section = 'health', '5-foods'   # the guide and the heading's anchor
"""

import re
from functools import lru_cache
from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

GUIDES_DIR = Path(settings.BASE_DIR) / 'docs' / 'admin-guide'
GUIDES = {'health': _('How to use the Health pages')}


# The guide's own "Contents" section duplicates the side panel inside the admin.
_CONTENTS_SECTION = re.compile(r'<h2 id="contents">.*?(?=<h2 )', re.S)
_CONTENTS_ITEM = re.compile(r'<li><a href="#contents">[^<]*</a></li>\s*')
_TOP_ITEM = re.compile(r'<a href="(#[^"]+)">(\d+)\. ([^<]+)</a>')      # "3. Plans"
_SUB_ITEM = re.compile(r'<a href="(#[^"]+)">(\d+\.\d+) ([^<]+)</a>')  # "5.4 Add a new food"


def _side_panel(toc: str) -> str:
    """The table of contents as the side panel: number badges, numbers split from titles."""
    toc = _CONTENTS_ITEM.sub('', toc)
    toc = _TOP_ITEM.sub(r'<a href="\1"><span class="admin-help-toc-num">\2</span>'
                        r'<span class="admin-help-toc-text">\3</span></a>', toc)
    return _SUB_ITEM.sub(r'<a href="\1"><span class="admin-help-toc-subnum">\2</span>'
                         r'<span class="admin-help-toc-text">\3</span></a>', toc)


@lru_cache(maxsize=16)
def _render(path: Path, mtime: float) -> tuple[str, str]:
    """(html, side panel). Cached until the file changes. The guides are our own trusted files."""
    md = markdown.Markdown(extensions=['tables', 'toc', 'fenced_code', 'sane_lists'],
                           extension_configs={'toc': {'toc_depth': '2-3'}})
    html = md.convert(path.read_text(encoding='utf-8'))
    return _CONTENTS_SECTION.sub('', html), _side_panel(md.toc)


def render_guide(guide: str) -> tuple[str, str]:
    if guide not in GUIDES:
        raise Http404('Unknown guide.')
    path = GUIDES_DIR / f'{guide}.md'
    if not path.exists():
        raise Http404('Unknown guide.')
    return _render(path, path.stat().st_mtime)


def help_url(guide: str, section: str = '') -> str:
    url = reverse('admin-help', args=[guide])
    return f'{url}#{section}' if section else url


def help_view(request, guide):
    """Wrapped with admin_site.admin_view in config/urls.py: staff only, inside the admin layout."""
    from django.contrib import admin

    html, toc = render_guide(guide)
    context = {**admin.site.each_context(request), 'title': GUIDES[guide], 'guide_html': html,
               'guide_toc': toc}
    return TemplateResponse(request, 'admin/help.html', context)


class HelpLinkMixin:
    """Adds a "How to use this page" button above the list and the form of a ModelAdmin."""

    help_guide: str = ''
    help_section: str = ''
    list_before_template = 'admin/help_link.html'
    change_form_before_template = 'admin/help_link.html'

    @property
    def help_url(self) -> str:
        return help_url(self.help_guide, self.help_section)
