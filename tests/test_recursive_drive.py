from types import SimpleNamespace

from services.drive import DriveService


def make_drive(pages):
    drive = object.__new__(DriveService)
    def list_files(**kwargs):
        folder = kwargs['q'].split("'")[1]
        kind = 'pdf' if "mimeType='application/pdf'" in kwargs['q'] else 'folders'
        page = kwargs.get('pageToken')
        return SimpleNamespace(execute=lambda: pages.get((folder, kind, page), {'files': []}))
    drive.api = SimpleNamespace(files=lambda: SimpleNamespace(list=list_files))
    return drive


def test_finds_pdf_in_nested_folder_after_paginated_empty_folders():
    invoice = {'id': 'invoice', 'name': 'factura.pdf', 'parents': ['block']}
    drive = make_drive({
        ('queue', 'folders', None): {'files': [{'id': 'empty', 'name': 'Vacía'}], 'nextPageToken': 'next'},
        ('queue', 'folders', 'next'): {'files': [{'id': 'campus', 'name': 'CAMPUS'}]},
        ('campus', 'folders', None): {'files': [{'id': 'block', 'name': 'BLOQUE A'}]},
        ('block', 'pdf', None): {'files': [invoice]},
    })
    assert drive.list_one_pdf('queue') == invoice


def test_direct_pdf_still_takes_priority():
    invoice = {'id': 'direct', 'name': 'factura.pdf'}
    drive = make_drive({('queue', 'pdf', None): {'files': [invoice]}})
    assert drive.list_one_pdf('queue') == invoice


def test_empty_nested_tree_returns_none():
    drive = make_drive({('queue', 'folders', None): {'files': [{'id': 'empty', 'name': 'Vacía'}]}})
    assert drive.list_one_pdf('queue') is None


def make_tree(pages):
    drive = object.__new__(DriveService)

    def list_files(**kwargs):
        folder = kwargs['q'].split("'")[1]
        page = kwargs.get('pageToken')
        return SimpleNamespace(execute=lambda: pages.get((folder, page), {'files': []}))

    drive.api = SimpleNamespace(files=lambda: SimpleNamespace(list=list_files))
    return drive


FOLDER = 'application/vnd.google-apps.folder'


def test_iter_pdfs_walks_every_subfolder_and_paginates():
    drive = make_tree({
        ('root', None): {'files': [
            {'id': 'sub', 'name': 'BLOQUE A', 'mimeType': FOLDER},
            {'id': 'p1', 'name': 'a.pdf', 'mimeType': 'application/pdf', 'parents': ['root']},
        ], 'nextPageToken': 'page2'},
        ('root', 'page2'): {'files': [
            {'id': 'p2', 'name': 'b.pdf', 'mimeType': 'application/pdf', 'parents': ['root']},
        ]},
        ('sub', None): {'files': [
            {'id': 'p3', 'name': 'c.pdf', 'mimeType': 'application/pdf', 'parents': ['sub']},
        ]},
    })
    assert [item['id'] for item in drive.iter_pdfs('root')] == ['p1', 'p2', 'p3']


def test_iter_pdfs_ignores_non_pdf_files_and_repeated_folders():
    drive = make_tree({
        ('root', None): {'files': [
            {'id': 'sheet', 'name': 'BD', 'mimeType': 'application/vnd.google-apps.spreadsheet'},
            {'id': 'loop', 'name': 'A', 'mimeType': FOLDER},
        ]},
        ('loop', None): {'files': [
            {'id': 'loop', 'name': 'A', 'mimeType': FOLDER},
            {'id': 'p1', 'name': 'a.pdf', 'mimeType': 'application/pdf'},
        ]},
    })
    assert [item['id'] for item in drive.iter_pdfs('root')] == ['p1']
