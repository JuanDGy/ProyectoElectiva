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
