#!/usr/bin/env python
# Copyright (c) 2011 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Cron script to generate usage notifications for volumes existing during
   the audit period.

   Together with the notifications generated by volumes
   create/delete/resize, over that time period, this allows an external
   system consuming usage notification feeds to calculate volume usage
   for each tenant.

   Time periods are specified as 'hour', 'month', 'day' or 'year'

   hour = previous hour. If run at 9:07am, will generate usage for 8-9am.
   month = previous month. If the script is run April 1, it will generate
           usages for March 1 through March 31.
   day = previous day. if run on July 4th, it generates usages for July 3rd.
   year = previous year. If run on Jan 1, it generates usages for
        Jan 1 through Dec 31 of the previous year.
"""

from __future__ import print_function

import datetime
import sys
import warnings

warnings.simplefilter('once', DeprecationWarning)

from oslo_config import cfg
from oslo_log import log as logging

from cinder import i18n
i18n.enable_lazy()
from cinder import context
from cinder import db
from cinder.i18n import _, _LE
from cinder import rpc
from cinder import utils
from cinder import version
import cinder.volume.utils


CONF = cfg.CONF
script_opts = [
    cfg.StrOpt('start_time',
               default=None,
               help="If this option is specified then the start time "
                    "specified is used instead of the start time of the "
                    "last completed audit period."),
    cfg.StrOpt('end_time',
               default=None,
               help="If this option is specified then the end time "
                    "specified is used instead of the end time of the "
                    "last completed audit period."),
    cfg.BoolOpt('send_actions',
                default=False,
                help="Send the volume and snapshot create and delete "
                     "notifications generated in the specified period."),
]
CONF.register_cli_opts(script_opts)


def main():
    admin_context = context.get_admin_context()
    CONF(sys.argv[1:], project='cinder',
         version=version.version_string())
    logging.setup(CONF, "cinder")
    LOG = logging.getLogger("cinder")
    rpc.init(CONF)
    begin, end = utils.last_completed_audit_period()
    if CONF.start_time:
        begin = datetime.datetime.strptime(CONF.start_time,
                                           "%Y-%m-%d %H:%M:%S")
    if CONF.end_time:
        end = datetime.datetime.strptime(CONF.end_time,
                                         "%Y-%m-%d %H:%M:%S")
    if not end > begin:
        msg = _("The end time (%(end)s) must be after the start "
                "time (%(start)s).") % {'start': begin,
                                        'end': end}
        LOG.error(msg)
        sys.exit(-1)
    LOG.debug("Starting volume usage audit")
    msg = _("Creating usages for %(begin_period)s until %(end_period)s")
    LOG.debug(msg, {"begin_period": str(begin), "end_period": str(end)})

    extra_info = {
        'audit_period_beginning': str(begin),
        'audit_period_ending': str(end),
    }

    volumes = db.volume_get_active_by_window(admin_context,
                                             begin,
                                             end)
    LOG.debug("Found %d volumes"), len(volumes)
    for volume_ref in volumes:
        try:
            LOG.debug("Send exists notification for <volume_id: "
                      "%(volume_id)s> <project_id %(project_id)s> "
                      "<%(extra_info)s>",
                      {'volume_id': volume_ref.id,
                       'project_id': volume_ref.project_id,
                       'extra_info': extra_info})
            cinder.volume.utils.notify_about_volume_usage(
                admin_context,
                volume_ref,
                'exists', extra_usage_info=extra_info)
        except Exception as exc_msg:
            LOG.exception(_LE("Exists volume notification failed: %s"),
                          exc_msg, resource=volume_ref)

        if (CONF.send_actions and
                volume_ref.created_at > begin and
                volume_ref.created_at < end):
            try:
                local_extra_info = {
                    'audit_period_beginning': str(volume_ref.created_at),
                    'audit_period_ending': str(volume_ref.created_at),
                }
                LOG.debug("Send create notification for "
                          "<volume_id: %(volume_id)s> "
                          "<project_id %(project_id)s> <%(extra_info)s>",
                          {'volume_id': volume_ref.id,
                           'project_id': volume_ref.project_id,
                           'extra_info': local_extra_info})
                cinder.volume.utils.notify_about_volume_usage(
                    admin_context,
                    volume_ref,
                    'create.start', extra_usage_info=local_extra_info)
                cinder.volume.utils.notify_about_volume_usage(
                    admin_context,
                    volume_ref,
                    'create.end', extra_usage_info=local_extra_info)
            except Exception as exc_msg:
                LOG.exception(_LE("Create volume notification failed: %s"),
                              exc_msg, resource=volume_ref)

        if (CONF.send_actions and volume_ref.deleted_at and
                volume_ref.deleted_at > begin and
                volume_ref.deleted_at < end):
            try:
                local_extra_info = {
                    'audit_period_beginning': str(volume_ref.deleted_at),
                    'audit_period_ending': str(volume_ref.deleted_at),
                }
                LOG.debug("Send delete notification for "
                          "<volume_id: %(volume_id)s> "
                          "<project_id %(project_id)s> <%(extra_info)s>",
                          {'volume_id': volume_ref.id,
                           'project_id': volume_ref.project_id,
                           'extra_info': local_extra_info})
                cinder.volume.utils.notify_about_volume_usage(
                    admin_context,
                    volume_ref,
                    'delete.start', extra_usage_info=local_extra_info)
                cinder.volume.utils.notify_about_volume_usage(
                    admin_context,
                    volume_ref,
                    'delete.end', extra_usage_info=local_extra_info)
            except Exception as exc_msg:
                LOG.exception(_LE("Delete volume notification failed: %s"),
                              exc_msg, resource=volume_ref)

    snapshots = db.snapshot_get_active_by_window(admin_context,
                                                 begin,
                                                 end)
    LOG.debug("Found %d snapshots"), len(snapshots)
    for snapshot_ref in snapshots:
        try:
            LOG.debug("Send notification for <snapshot_id: %(snapshot_id)s> "
                      "<project_id %(project_id)s> <%(extra_info)s>",
                      {'snapshot_id': snapshot_ref.id,
                       'project_id': snapshot_ref.project_id,
                       'extra_info': extra_info})
            cinder.volume.utils.notify_about_snapshot_usage(admin_context,
                                                            snapshot_ref,
                                                            'exists',
                                                            extra_info)
        except Exception as exc_msg:
            LOG.exception(_LE("Exists snapshot notification failed: %s"),
                          exc_msg, resource=snapshot_ref)

        if (CONF.send_actions and
                snapshot_ref.created_at > begin and
                snapshot_ref.created_at < end):
            try:
                local_extra_info = {
                    'audit_period_beginning': str(snapshot_ref.created_at),
                    'audit_period_ending': str(snapshot_ref.created_at),
                }
                LOG.debug("Send create notification for "
                          "<snapshot_id: %(snapshot_id)s> "
                          "<project_id %(project_id)s> <%(extra_info)s>",
                          {'snapshot_id': snapshot_ref.id,
                           'project_id': snapshot_ref.project_id,
                           'extra_info': local_extra_info})
                cinder.volume.utils.notify_about_snapshot_usage(
                    admin_context,
                    snapshot_ref,
                    'create.start', extra_usage_info=local_extra_info)
                cinder.volume.utils.notify_about_snapshot_usage(
                    admin_context,
                    snapshot_ref,
                    'create.end', extra_usage_info=local_extra_info)
            except Exception as exc_msg:
                LOG.exception(_LE("Create snapshot notification failed: %s"),
                              exc_msg, resource=snapshot_ref)

        if (CONF.send_actions and snapshot_ref.deleted_at and
                snapshot_ref.deleted_at > begin and
                snapshot_ref.deleted_at < end):
            try:
                local_extra_info = {
                    'audit_period_beginning': str(snapshot_ref.deleted_at),
                    'audit_period_ending': str(snapshot_ref.deleted_at),
                }
                LOG.debug("Send delete notification for "
                          "<snapshot_id: %(snapshot_id)s> "
                          "<project_id %(project_id)s> <%(extra_info)s>",
                          {'snapshot_id': snapshot_ref.id,
                           'project_id': snapshot_ref.project_id,
                           'extra_info': local_extra_info})
                cinder.volume.utils.notify_about_snapshot_usage(
                    admin_context,
                    snapshot_ref,
                    'delete.start', extra_usage_info=local_extra_info)
                cinder.volume.utils.notify_about_snapshot_usage(
                    admin_context,
                    snapshot_ref,
                    'delete.end', extra_usage_info=local_extra_info)
            except Exception as exc_msg:
                LOG.exception(_LE("Delete snapshot notification failed: %s"),
                              exc_msg, resource=snapshot_ref)

    LOG.debug("Volume usage audit completed")
