USE jimv;

ALTER TABLE guest ADD COLUMN vlan_id INT NOT NULL DEFAULT -1;
ALTER TABLE guest ADD INDEX (vlan_id);


CREATE TABLE IF NOT EXISTS vlan(
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    vlan_id INT NOT NULL UNIQUE,
    label VARCHAR(255) NOT NULL,
    description TEXT,
    create_time BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (id))
    ENGINE=Innodb
    DEFAULT CHARSET=utf8;

ALTER TABLE vlan ADD INDEX (vlan_id);
ALTER TABLE vlan ADD INDEX (label);
