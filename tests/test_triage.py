from pipeline.triage import classify


def test_energy():
    assert classify('factura.pdf', 'Consumo energía activa 100 kWh') == 'ENERGIA'


def test_water_is_out_of_scope():
    assert classify('factura.pdf', 'Servicio de acueducto y alcantarillado') == 'SIN_CLASIFICAR'


def test_energy_with_other_charges_stays_energy():
    assert classify('factura.pdf', 'Acueducto incluido. Consumo de energía 150 kWh') == 'ENERGIA'


def test_filename_does_not_override_energy_text():
    assert classify('COPIA_AGUA_factura.pdf', '100 kWh') == 'ENERGIA'


def test_unclassified():
    assert classify('otro.pdf', 'Documento administrativo') == 'SIN_CLASIFICAR'
