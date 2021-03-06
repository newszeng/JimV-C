#!/usr/bin/env python
# -*- coding: utf-8 -*-


from math import ceil

from flask import Blueprint, request, url_for
import json
import requests
from uuid import uuid4
import jimit as ji

from jimvc.models import Guest, DiskState, Host
from jimvc.models.initialize import dev_table
from jimvc.models import Config
from jimvc.models import Disk
from jimvc.models import Rules
from jimvc.models import Utils
from jimvc.models import OSTemplateImage
from jimvc.models import StorageMode

from base import Base


__author__ = 'James Iter'
__date__ = '2017/4/24'
__contact__ = 'james.iter.cn@gmail.com'
__copyright__ = '(c) 2017 by James Iter.'


blueprint = Blueprint(
    'api_disk',
    __name__,
    url_prefix='/api/disk'
)

blueprints = Blueprint(
    'api_disks',
    __name__,
    url_prefix='/api/disks'
)


disk_base = Base(the_class=Disk, the_blueprint=blueprint, the_blueprints=blueprints)


@Utils.dumps2response
def r_create():

    args_rules = [
        Rules.DISK_SIZE.value,
        Rules.REMARK.value,
        Rules.QUANTITY.value
    ]

    config = Config()
    config.id = 1
    config.get()

    # 非共享模式，必须指定 node_id
    if config.storage_mode not in [StorageMode.shared_mount.value, StorageMode.ceph.value,
                                   StorageMode.glusterfs.value]:
        args_rules.append(
            Rules.NODE_ID.value
        )

    try:
        ji.Check.previewing(args_rules, request.json)

        size = request.json['size']
        quantity = request.json['quantity']

        ret = dict()
        ret['state'] = ji.Common.exchange_state(20000)

        # 如果是共享模式，则让负载最轻的计算节点去创建磁盘
        if config.storage_mode in [StorageMode.shared_mount.value, StorageMode.ceph.value,
                                   StorageMode.glusterfs.value]:
            available_hosts = Host.get_available_hosts()

            if available_hosts.__len__() == 0:
                ret['state'] = ji.Common.exchange_state(50351)
                return ret

            # 在可用计算节点中平均分配任务
            chosen_host = available_hosts[quantity % available_hosts.__len__()]
            request.json['node_id'] = chosen_host['node_id']

        node_id = request.json['node_id']

        if size < 1:
            ret['state'] = ji.Common.exchange_state(41255)
            return ret

        while quantity:
            quantity -= 1
            disk = Disk()
            disk.guest_uuid = ''
            disk.size = size
            disk.uuid = uuid4().__str__()
            disk.remark = request.json.get('remark', '')
            disk.node_id = int(node_id)
            disk.sequence = -1
            disk.format = 'qcow2'
            disk.path = config.storage_path + '/' + disk.uuid + '.' + disk.format
            disk.quota(config=config)

            message = {
                '_object': 'disk',
                'action': 'create',
                'uuid': disk.uuid,
                'storage_mode': config.storage_mode,
                'dfs_volume': config.dfs_volume,
                'node_id': disk.node_id,
                'image_path': disk.path,
                'size': disk.size
            }

            Utils.emit_instruction(message=json.dumps(message, ensure_ascii=False))

            disk.create()

        return ret

    except ji.PreviewingError, e:
        return json.loads(e.message)


@Utils.dumps2response
def r_resize(uuid, size):

    args_rules = [
        Rules.UUID.value,
        Rules.DISK_SIZE_STR.value
    ]

    try:
        ji.Check.previewing(args_rules, {'uuid': uuid, 'size': size})

        disk = Disk()
        disk.uuid = uuid
        disk.get_by('uuid')

        ret = dict()
        ret['state'] = ji.Common.exchange_state(20000)

        if disk.size >= int(size):
            ret['state'] = ji.Common.exchange_state(41257)
            return ret

        config = Config()
        config.id = 1
        config.get()

        disk.size = int(size)
        disk.quota(config=config)
        # 将在事件返回层(models/event_processor.py:224 附近)，更新数据库中 disk 对象

        message = {
            '_object': 'disk',
            'action': 'resize',
            'uuid': disk.uuid,
            'guest_uuid': disk.guest_uuid,
            'storage_mode': config.storage_mode,
            'size': disk.size,
            'dfs_volume': config.dfs_volume,
            'node_id': disk.node_id,
            'image_path': disk.path,
            'disks': [disk.__dict__],
            'passback_parameters': {'size': disk.size}
        }

        if config.storage_mode in [StorageMode.shared_mount.value, StorageMode.ceph.value,
                                   StorageMode.glusterfs.value]:
            message['node_id'] = Host.get_lightest_host()['node_id']

        if disk.guest_uuid.__len__() == 36:
            message['device_node'] = dev_table[disk.sequence]

        Utils.emit_instruction(message=json.dumps(message, ensure_ascii=False))

        return ret

    except ji.PreviewingError, e:
        return json.loads(e.message)


@Utils.dumps2response
def r_delete(uuids):

    args_rules = [
        Rules.UUIDS.value
    ]

    try:
        ji.Check.previewing(args_rules, {'uuids': uuids})

        ret = dict()
        ret['state'] = ji.Common.exchange_state(20000)

        disk = Disk()

        # 检测所指定的 UUDIs 磁盘都存在
        for uuid in uuids.split(','):
            disk.uuid = uuid
            disk.get_by('uuid')

            # 判断磁盘是否与虚拟机处于离状态
            if disk.state not in [DiskState.idle.value, DiskState.dirty.value]:
                ret['state'] = ji.Common.exchange_state(41256)
                return ret

        config = Config()
        config.id = 1
        config.get()

        # 执行删除操作
        for uuid in uuids.split(','):
            disk.uuid = uuid
            disk.get_by('uuid')

            message = {
                '_object': 'disk',
                'action': 'delete',
                'uuid': disk.uuid,
                'storage_mode': config.storage_mode,
                'dfs_volume': config.dfs_volume,
                'node_id': disk.node_id,
                'image_path': disk.path
            }

            if config.storage_mode in [StorageMode.shared_mount.value, StorageMode.ceph.value,
                                       StorageMode.glusterfs.value]:
                message['node_id'] = Host.get_lightest_host()['node_id']

            Utils.emit_instruction(message=json.dumps(message, ensure_ascii=False))

        return ret

    except ji.PreviewingError, e:
        return json.loads(e.message)


def add_device(func):
    from functools import wraps

    @wraps(func)
    def _add_device(*args, **kwargs):
        ret = func(*args, **kwargs)
        if ret['data'].__len__() > 0:
            if isinstance(ret['data'], list):
                for i, item in enumerate(ret['data']):
                    ret['data'][i][u'device'] = u'/dev/' + dev_table[item['sequence']]

                    if item['sequence'] < 0:
                        ret['data'][i][u'device'] = None

            elif isinstance(ret['data'], dict):
                ret['data'][u'device'] = u'/dev/' + dev_table[ret['data']['sequence']]

                if ret['data']['sequence'] < 0:
                    ret['data'][u'device'] = None

            else:
                raise json.dumps(ret)

        return ret

    return _add_device


@Utils.dumps2response
@add_device
def r_get(uuids):
    return disk_base.get(ids=uuids, ids_rule=Rules.UUIDS.value, by_field='uuid')


@Utils.dumps2response
@add_device
def r_get_by_filter():
    return disk_base.get_by_filter()


@Utils.dumps2response
@add_device
def r_content_search():
    return disk_base.content_search()


@Utils.dumps2response
def r_update(uuids):

    ret = dict()
    ret['state'] = ji.Common.exchange_state(20000)
    ret['data'] = list()

    args_rules = [
        Rules.UUIDS.value
    ]

    if 'remark' in request.json:
        args_rules.append(
            Rules.REMARK.value
        )

    if 'iops' in request.json:
        args_rules.append(
            Rules.IOPS.value
        )

    if 'iops_rd' in request.json:
        args_rules.append(
            Rules.IOPS_RD.value
        )

    if 'iops_wr' in request.json:
        args_rules.append(
            Rules.IOPS_WR.value
        )

    if 'iops_max' in request.json:
        args_rules.append(
            Rules.IOPS_MAX.value
        )

    if 'iops_max_length' in request.json:
        args_rules.append(
            Rules.IOPS_MAX_LENGTH.value
        )

    if 'bps' in request.json:
        args_rules.append(
            Rules.BPS.value
        )

    if 'bps_rd' in request.json:
        args_rules.append(
            Rules.BPS_RD.value
        )

    if 'bps_wr' in request.json:
        args_rules.append(
            Rules.BPS_WR.value
        )

    if 'bps_max' in request.json:
        args_rules.append(
            Rules.BPS_MAX.value
        )

    if 'bps_max_length' in request.json:
        args_rules.append(
            Rules.BPS_MAX_LENGTH.value
        )

    if args_rules.__len__() < 2:
        return ret

    request.json['uuids'] = uuids

    need_update_quota = False
    need_update_quota_parameters = ['iops', 'iops_rd', 'iops_wr', 'iops_max', 'iops_max_length',
                                    'bps', 'bps_rd', 'bps_wr', 'bps_max', 'bps_max_length']

    if filter(lambda p: p in request.json, need_update_quota_parameters).__len__() > 0:
        need_update_quota = True

    try:
        ji.Check.previewing(args_rules, request.json)

        disk = Disk()

        # 检测所指定的 UUDIs 磁盘都存在
        for uuid in uuids.split(','):
            disk.uuid = uuid
            disk.get_by('uuid')

        for uuid in uuids.split(','):
            disk.uuid = uuid
            disk.get_by('uuid')
            disk.remark = request.json.get('remark', disk.remark)
            disk.iops = request.json.get('iops', disk.iops)
            disk.iops_rd = request.json.get('iops_rd', disk.iops_rd)
            disk.iops_wr = request.json.get('iops_wr', disk.iops_wr)
            disk.iops_max = request.json.get('iops_max', disk.iops_max)
            disk.iops_max_length = request.json.get('iops_max_length', disk.iops_max_length)
            disk.bps = request.json.get('bps', disk.bps)
            disk.bps_rd = request.json.get('bps_rd', disk.bps_rd)
            disk.bps_wr = request.json.get('bps_wr', disk.bps_wr)
            disk.bps_max = request.json.get('bps_max', disk.bps_max)
            disk.bps_max_length = request.json.get('bps_max_length', disk.bps_max_length)
            disk.update()
            disk.get()

            if disk.sequence >= 0 and need_update_quota:
                message = {
                    '_object': 'disk',
                    'action': 'quota',
                    'uuid': disk.uuid,
                    'guest_uuid': disk.guest_uuid,
                    'node_id': disk.node_id,
                    'disks': [disk.__dict__]
                }

                Utils.emit_instruction(message=json.dumps(message))

            ret['data'].append(disk.__dict__)

        return ret

    except ji.PreviewingError, e:
        return json.loads(e.message)


@Utils.dumps2response
def r_distribute_count():
    from jimvc.models import Disk
    rows, count = Disk.get_all()

    ret = dict()
    ret['state'] = ji.Common.exchange_state(20000)

    ret['data'] = {
        'kind': {'system': 0, 'data_mounted': 0, 'data_idle': 0},
        'total_size': 0,
        'disks': rows.__len__()
    }

    for disk in rows:
        if disk['sequence'] == 0:
            ret['data']['kind']['system'] += 1

        elif disk['sequence'] < 0:
            ret['data']['kind']['data_idle'] += 1

        else:
            ret['data']['kind']['data_mounted'] += 1

        ret['data']['total_size'] += disk['size']

    return ret


@Utils.dumps2response
def r_show():
    args = list()

    page = request.args.get('page', 1)
    if page == '':
        page = 1
    page = int(page)

    page_size = int(request.args.get('page_size', 20))
    keyword = request.args.get('keyword', None)
    show_area = request.args.get('show_area', 'unmount')
    guest_uuid = request.args.get('guest_uuid', None)
    sequence = request.args.get('sequence', None)
    order_by = request.args.get('order_by', None)
    order = request.args.get('order', None)
    filters = list()

    if page is not None:
        args.append('page=' + page.__str__())

    if page_size is not None:
        args.append('page_size=' + page_size.__str__())

    if keyword is not None:
        args.append('keyword=' + keyword.__str__())

    if guest_uuid is not None:
        filters.append('guest_uuid:in:' + guest_uuid.__str__())
        show_area = 'all'

    if sequence is not None:
        filters.append('sequence:in:' + sequence.__str__())
        show_area = 'all'

    if show_area in ['unmount', 'data_disk', 'all']:
        if show_area == 'unmount':
            filters.append('sequence:eq:-1')

        elif show_area == 'data_disk':
            filters.append('sequence:gt:0')

        else:
            pass

    else:
        # 与前端页面相照应，首次打开时，默认只显示未挂载的磁盘
        filters.append('sequence:eq:-1')

    if order_by is not None:
        args.append('order_by=' + order_by)

    if order is not None:
        args.append('order=' + order)

    if filters.__len__() > 0:
        args.append('filter=' + ','.join(filters))

    hosts_url = url_for('api_hosts.r_get_by_filter', _external=True)
    disks_url = url_for('api_disks.r_get_by_filter', _external=True)

    if keyword is not None:
        disks_url = url_for('api_disks.r_content_search', _external=True)
        # 关键字检索，不支持显示域过滤
        show_area = 'all'

    hosts_ret = requests.get(url=hosts_url, cookies=request.cookies)
    hosts_ret = json.loads(hosts_ret.content)

    hosts_mapping_by_node_id = dict()
    for host in hosts_ret['data']:
        hosts_mapping_by_node_id[int(host['node_id'])] = host

    if args.__len__() > 0:
        disks_url = disks_url + '?' + '&'.join(args)

    disks_ret = requests.get(url=disks_url, cookies=request.cookies)
    disks_ret = json.loads(disks_ret.content)

    guests_uuid = list()
    disks_uuid = list()

    for disk in disks_ret['data']:
        disks_uuid.append(disk['uuid'])

        if disk['guest_uuid'].__len__() == 36:
            guests_uuid.append(disk['guest_uuid'])

    if guests_uuid.__len__() > 0:
        guests, _ = Guest.get_by_filter(filter_str='uuid:in:' + ','.join(guests_uuid))

        guests_uuid_mapping = dict()
        for guest in guests:
            guests_uuid_mapping[guest['uuid']] = guest

        for i, disk in enumerate(disks_ret['data']):
            if disk['guest_uuid'].__len__() == 36:
                disks_ret['data'][i]['guest'] = guests_uuid_mapping[disk['guest_uuid']]

    if disks_uuid.__len__() > 0:
        snapshots_id_mapping_by_disks_uuid_url = url_for('api_snapshots.r_get_snapshots_by_disks_uuid',
                                                         disks_uuid=','.join(disks_uuid), _external=True)
        snapshots_id_mapping_by_disks_uuid_ret = requests.get(url=snapshots_id_mapping_by_disks_uuid_url,
                                                              cookies=request.cookies)
        snapshots_id_mapping_by_disks_uuid_ret = json.loads(snapshots_id_mapping_by_disks_uuid_ret.content)

        snapshots_id_mapping_by_disk_uuid = dict()

        for snapshot_id_mapping_by_disk_uuid in snapshots_id_mapping_by_disks_uuid_ret['data']:

            disk_uuid = snapshot_id_mapping_by_disk_uuid['disk_uuid']
            snapshot_id = snapshot_id_mapping_by_disk_uuid['snapshot_id']

            if disk_uuid not in snapshots_id_mapping_by_disk_uuid:
                snapshots_id_mapping_by_disk_uuid[disk_uuid] = list()

            snapshots_id_mapping_by_disk_uuid[disk_uuid].append(snapshot_id)

        for i, disk in enumerate(disks_ret['data']):
            if disk['uuid'] in snapshots_id_mapping_by_disk_uuid:
                disks_ret['data'][i]['snapshot'] = snapshots_id_mapping_by_disk_uuid[disk['uuid']]

    config = Config()
    config.id = 1
    config.get()

    show_on_host = False
    if config.storage_mode == StorageMode.local.value:
        show_on_host = True

    last_page = int(ceil(disks_ret['paging']['total'] / float(page_size)))
    page_length = 5
    pages = list()
    if page < int(ceil(page_length / 2.0)):
        for i in range(1, page_length + 1):
            pages.append(i)
            if i == last_page or last_page == 0:
                break

    elif last_page - page < page_length / 2:
        for i in range(last_page - page_length + 1, last_page + 1):
            if i < 1:
                continue
            pages.append(i)

    else:
        for i in range(page - page_length / 2, page + int(ceil(page_length / 2.0))):
            pages.append(i)
            if i == last_page or last_page == 0:
                break

    ret = dict()
    ret['state'] = ji.Common.exchange_state(20000)

    ret['data'] = {
        'disks': disks_ret['data'],
        'hosts_mapping_by_node_id': hosts_mapping_by_node_id,
        'order_by': order_by,
        'order': order,
        'show_area': show_area,
        'config': config.__dict__,
        'show_on_host': show_on_host,
        'paging': disks_ret['paging'],
        'page': page,
        'page_size': page_size,
        'keyword': keyword,
        'pages': pages,
        'last_page': last_page
    }

    return ret


@Utils.dumps2response
def r_detail(uuid):
    disk = Disk()
    disk.uuid = uuid
    disk.get_by(field='uuid')
    disk.wrap_device(dev_table=dev_table)

    guest = None
    os_template_image = None

    config = Config()
    config.id = 1
    config.get()

    if disk.sequence != -1:
        guest = Guest()
        guest.uuid = disk.guest_uuid
        guest.get_by('uuid')

        os_template_image = OSTemplateImage()
        os_template_image.id = guest.os_template_image_id
        os_template_image.get()

        guest = guest.__dict__
        os_template_image = os_template_image.__dict__

    ret = dict()
    ret['state'] = ji.Common.exchange_state(20000)

    ret['data'] = {
        'guest': guest,
        'os_template_image': os_template_image,
        'disk': disk.__dict__,
        'config': config.__dict__
    }

    return ret


