from services.drive import DriveService


def test_office_from_nested_history_folder():
    drive=object.__new__(DriveService)
    folders={'block':{'name':'BLOQUE A','parents':['principal']},
             'principal':{'name':'PRINCIPAL','parents':['root']}}
    drive.get_metadata=lambda fid: folders[fid]
    assert drive.source_office({'parents':['block']},'root')=='PRINCIPAL / BLOQUE A'


def test_office_is_not_guessed_from_queue_folder():
    drive=object.__new__(DriveService)
    folders={'queue':{'name':'01A','parents':['project']},'project':{'name':'Proyecto','parents':[]}}
    drive.get_metadata=lambda fid: folders[fid]
    assert drive.source_office({'parents':['queue']},'history')==''


def test_copied_invoice_retains_original_office():
    drive=object.__new__(DriveService)
    assert drive.source_office({'appProperties':{'sourceOffice':'CRISANTO LUQUE'},'parents':['queue']},'root')=='CRISANTO LUQUE'
