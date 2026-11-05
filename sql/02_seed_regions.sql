-- 02_seed_regions.sql — province → region → macro-area (ISTAT grouping). Re-runnable.
SET search_path TO fuel_dw;

INSERT INTO dim_region (province, region, macro_area) VALUES
('AO','Valle d''Aosta','North'),
('AL','Piemonte','North'),('AT','Piemonte','North'),('BI','Piemonte','North'),('CN','Piemonte','North'),('NO','Piemonte','North'),('TO','Piemonte','North'),('VB','Piemonte','North'),('VC','Piemonte','North'),
('BG','Lombardia','North'),('BS','Lombardia','North'),('CO','Lombardia','North'),('CR','Lombardia','North'),('LC','Lombardia','North'),('LO','Lombardia','North'),('MB','Lombardia','North'),('MI','Lombardia','North'),('MN','Lombardia','North'),('PV','Lombardia','North'),('SO','Lombardia','North'),('VA','Lombardia','North'),
('BZ','Trentino-Alto Adige','North'),('TN','Trentino-Alto Adige','North'),
('BL','Veneto','North'),('PD','Veneto','North'),('RO','Veneto','North'),('TV','Veneto','North'),('VE','Veneto','North'),('VI','Veneto','North'),('VR','Veneto','North'),
('GO','Friuli-Venezia Giulia','North'),('PN','Friuli-Venezia Giulia','North'),('TS','Friuli-Venezia Giulia','North'),('UD','Friuli-Venezia Giulia','North'),
('GE','Liguria','North'),('IM','Liguria','North'),('SP','Liguria','North'),('SV','Liguria','North'),
('BO','Emilia-Romagna','North'),('FC','Emilia-Romagna','North'),('FE','Emilia-Romagna','North'),('MO','Emilia-Romagna','North'),('PC','Emilia-Romagna','North'),('PR','Emilia-Romagna','North'),('RA','Emilia-Romagna','North'),('RE','Emilia-Romagna','North'),('RN','Emilia-Romagna','North'),
('AR','Toscana','Centre'),('FI','Toscana','Centre'),('GR','Toscana','Centre'),('LI','Toscana','Centre'),('LU','Toscana','Centre'),('MS','Toscana','Centre'),('PI','Toscana','Centre'),('PO','Toscana','Centre'),('PT','Toscana','Centre'),('SI','Toscana','Centre'),
('PG','Umbria','Centre'),('TR','Umbria','Centre'),
('AN','Marche','Centre'),('AP','Marche','Centre'),('FM','Marche','Centre'),('MC','Marche','Centre'),('PU','Marche','Centre'),
('FR','Lazio','Centre'),('LT','Lazio','Centre'),('RI','Lazio','Centre'),('RM','Lazio','Centre'),('VT','Lazio','Centre'),
('AQ','Abruzzo','South & Islands'),('CH','Abruzzo','South & Islands'),('PE','Abruzzo','South & Islands'),('TE','Abruzzo','South & Islands'),
('CB','Molise','South & Islands'),('IS','Molise','South & Islands'),
('AV','Campania','South & Islands'),('BN','Campania','South & Islands'),('CE','Campania','South & Islands'),('NA','Campania','South & Islands'),('SA','Campania','South & Islands'),
('BA','Puglia','South & Islands'),('BR','Puglia','South & Islands'),('BT','Puglia','South & Islands'),('FG','Puglia','South & Islands'),('LE','Puglia','South & Islands'),('TA','Puglia','South & Islands'),
('MT','Basilicata','South & Islands'),('PZ','Basilicata','South & Islands'),
('CS','Calabria','South & Islands'),('CZ','Calabria','South & Islands'),('KR','Calabria','South & Islands'),('RC','Calabria','South & Islands'),('VV','Calabria','South & Islands'),
('AG','Sicilia','South & Islands'),('CL','Sicilia','South & Islands'),('CT','Sicilia','South & Islands'),('EN','Sicilia','South & Islands'),('ME','Sicilia','South & Islands'),('PA','Sicilia','South & Islands'),('RG','Sicilia','South & Islands'),('SR','Sicilia','South & Islands'),('TP','Sicilia','South & Islands'),
('CA','Sardegna','South & Islands'),('NU','Sardegna','South & Islands'),('OR','Sardegna','South & Islands'),('SS','Sardegna','South & Islands'),('SU','Sardegna','South & Islands')
ON CONFLICT (province) DO NOTHING;
