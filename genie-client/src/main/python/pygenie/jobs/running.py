"""
genie.jobs.running

This module implements the RunningJob model.

"""


from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import re
import sys
import time

from functools import wraps

from ..conf import GenieConf
from ..utils import dttm_to_epoch


logger = logging.getLogger('com.netflix.genie.jobs.running')

RUNNING_STATUSES = {
    'INIT',
    'RUNNING',
    'init',
    'running'
}

INFO_SECTIONS = {
    'applications',
    'cluster',
    'command',
    'execution',
    'job',
    'request'
}


def get_from_info(info_key, info_section, update_if_running=False):
    """
    Get info_key from info dict.

    If the info_key is not present in the info dict, will get info_section from
    Genie.

    If update_if_running=True, then will go get info_section from Genie even if
    the info_key is present in the info dict.
    """

    def decorator(func):

        @wraps(func)
        def wrapper(*args, **kwargs):
            """Wraps func."""

            self = args[0]

            if info_key not in self.info:
                self._update_info(info_section)
            elif update_if_running:
                # don't get status unless have to to limit HTTP requests
                status = (self.status or 'INIT').upper()
                if status in RUNNING_STATUSES:
                    self._update_info(info_section)

            return self.info.get(info_key)

        return wrapper

    return decorator


class RunningJob(object):
    """RunningJob."""

    def __init__(self, job_id, adapter=None, conf=None, info=None):
        self._cached_genie_log = None
        self._cached_stderr = None
        self._conf = conf or GenieConf()
        self._info = info or dict()
        self._job_id = job_id
        self._status = self._info.get('status') or None
        self._sys_stream = None

        # get_adapter_version is set in main __init__.py to get around circular imports
        self._adapter = adapter \
            or get_adapter_for_version(self._conf.genie.version)(conf=self._conf)

        stream = self._conf.get('genie.progress_stream', 'stdout').lower()
        if stream in {'stderr', 'stdout'}:
            self._sys_stream = getattr(sys, stream)

    def __getattr__(self, attr):
        if attr in self.info:
            return self.info.get(attr)
        raise AttributeError("'RunningJob' object has no attribute '{}'" \
            .format(attr))

    def __repr__(self):
        return '{cls}("{job_id}", adapter={adapter})'.format(
            cls=self.__class__.__name__,
            job_id=self._job_id,
            adapter=self._adapter
        )

    def __convert_dttm_to_epoch(self, info_key):
        epoch = 0
        dttm = self.info.get(info_key, '1970-01-01T00:00:00Z')
        if dttm is not None and re.search(r'\.\d\d\dZ', dttm):
            epoch = dttm_to_epoch(dttm, frmt='%Y-%m-%dT%H:%M:%S.%fZ')
        elif dttm is not None:
            epoch = dttm_to_epoch(dttm)
        return epoch * 1000

    def _update_info(self, info_section=None):
        assert (info_section is None) or (info_section in INFO_SECTIONS), \
            "invalid info_section '{}' (should be None or one of {})" \
                .format(info_section, INFO_SECTIONS)

        kwargs = {info_section: True} if info_section else dict()

        data = self._adapter.get_info_for_rj(self._job_id, **kwargs)

        self._info.update(data)

    @property
    def info(self):
        return self._info

    @property
    @get_from_info('cluster_name', info_section='cluster')
    def cluster_name(self):
        """
        Get the name of the cluster the job was executed on.

        Example:
            >>> running_job.cluster_name
            u'slacluster'

        Returns:
            str: The cluster name.
        """

    @property
    def cmd_args(self):
        """
        Get the job's command line execution.

        Example:
            >>> running_job.cmd_args
            u'-f my_script.pig -p dateint=20140101'

        Returns:
            str: The command line execution.
        """

        return self.command_args

    @property
    @get_from_info('command_args', info_section='job')
    def command_args(self):
        """
        Get the job's command line execution.

        Example:
            >>> running_job.command_args
            u'-f my_script.pig -p dateint=20140101'

        Returns:
            str: The command line execution.
        """

    @property
    @get_from_info('description', info_section='job')
    def description(self):
        """
        Get the job description.

        Example:
            >>> running_job.description
            'This is the job description'

        Returns:
            str: The job description.
        """

    @property
    def duration(self):
        """
        Get the duration of the job's execution in milliseconds.

        A negative duration means the job is still running.

        Example:
            >>> running_job.duration
            1793
            >>> running_job.duration # job is still running
            -2182

        Returns:
            int: The duration of job execution in milliseconds.
        """

        return self.finish_time - self.start_time

    @property
    @get_from_info('file_dependencies', info_section='request')
    def file_dependencies(self):
        """
        Get the job's file dependencies.

        Example:
            >>> running_job.file_dependencies
            [u'x://file1.py']

        Returns:
            list: A list of the file dependencies.
        """

    @property
    def finish_time(self):
        """
        Get the job's finish epoch time (milliseconds). 0 means the job is still
        running.

        Example:
            >>> running_job.finish_time
            1234567890

        Returns:
            int: The finish time in epoch (milliseconds).
        """

        status = self.status

        if ('finished' not in self.info) \
                or status in RUNNING_STATUSES \
                or status is None:
            self._update_info('job')

        return self.__convert_dttm_to_epoch('finished')

    def genie_log(self, iterator=False):
        """
        Get the job's Genie log as either an iterator or full text.

        Example:
            >>> running_job.genie_log()
            '...'
            >>> for l in running_job.genie_log(iterator=True):
            >>>     print(l)

        Args:
            iterator (bool, optional): Set to True if want to return as iterator.

        Returns:
            str or iterator.
        """

        if self.is_done:
            if not self._cached_genie_log:
                self._cached_genie_log = self._adapter.get_genie_log(self._job_id)
            return self._cached_genie_log.split('\n') if iterator \
                else self._cached_genie_log
        return self._adapter.get_genie_log(self._job_id, iterator=iterator)

    def get_log(self, log_path, iterator=False):
        """
        Get the specified job's log as either an iterator or full text.

        Example:
            >>> running_job.get_log('genie/logs/env.log')
            '...'
            >>> for l in running_job.get_log('genie/logs/env.log', iterator=True):
            >>>     print(l)

        Args:
            log_path (str): The relative log path to the job's output directory.
            iterator (bool, optional): Set to True if want to return as iterator.

        Returns:
            str or iterator.
        """

        return self._adapter.get_log(self._job_id, log_path, iterator=iterator)

    @property
    def is_done(self):
        """
        Is the job done running?

        Example:
            >>> print running_job.is_done
            True

        Returns:
            boolean: True if the job is done, False if still running.
        """

        return self.status not in RUNNING_STATUSES

    @property
    def is_successful(self):
        """
        Did the job complete successfully?

        Example:
            >>> print running_job.is_successful
            False

        Returns:
            boolean: True if job completed successfully, False otherwise.
        """

        return self.status == 'SUCCEEDED'

    @property
    @get_from_info('id', info_section='job')
    def job_id(self):
        """
        Get the job's id.

        Example:
            >>> running_job.job_id
            u'1234-abcd-5678'

        Returns:
            str: The job id.
        """

    @property
    @get_from_info('name', info_section='job')
    def job_name(self):
        """
        Get the job's name.

        Example:
            >>> running_job.job_name
            u'my_job'

        Returns:
            str: Job name.
        """

    @property
    def job_type(self):
        """
        Get the job's type.

        Example:
            >>> running_job.job_type
            u'PIG'

        Returns:
            str: Job type.
        """

        if 'command_name' not in self.info:
            self._update_info('command')

        if 'hive' in (self.info.get('command_name', '') or '').lower():
            return 'HIVE'
        return self.info.get('command_name').upper()

    @property
    @get_from_info('job_link', info_section='job')
    def job_link(self):
        """
        Get the link for the job.

        Example:
            >>> print running_job.job_link
            'http://localhost/genie/1234-abcd'

        Returns:
            str: The link to the job.
        """

    @property
    @get_from_info('json_link', info_section='job')
    def json_link(self):
        """
        Get the link for the job json.

        Example:
            >>> print running_job.json_link
            'http://localhost/api/v3/jobs/1234-abcd'

        Returns:
            str: The link to the job json.
        """

    @property
    @get_from_info('kill_uri', info_section='job')
    def kill_uri(self):
        """
        Get the uri to kill the job.

        Sending a DELETE request to this uri will kill the job.

        Example:
            >>> print running_job.kill_uri
            'http://localhost/genie/1234-abcd'

        Returns:
            str: The kill URI.
        """

    def kill(self):
        """
        Kill the job.

        Example:
            >>> running_job.kill()

        Returns:
            request Response.
        """

        if self.info.get('kill_uri'):
            resp = self._adapter.kill_job(kill_uri=self.kill_uri)
        else:
            resp = self._adapter.kill_job(job_id=self._job_id)
        return resp

    @property
    @get_from_info('output_uri', info_section='job')
    def output_uri(self):
        """
        Get the output uri for the job.

        Example:
            >>> running_job.output_uri
            'http://localhost/genie/1234-abcd/output'

        Returns:
            str: The output URI.
        """

    @property
    @get_from_info('request_data', info_section='request')
    def request_data(self):
        """
        Get the JSON of the job submission request sent to Genie.

        Example:
            >>> running_job.request_data
            {...}

        Returns:
            dict: JSON of the job submission request.
        """

    @property
    def start_time(self):
        """
        Get the job's start epoch time (milliseconds).

        Example:
            >>> running_job.start_time
            1234567890

        Returns:
            int: The start time in epoch (milliseconds).
        """

        if ('started' not in self.info) \
                or self.info.get('started') is None:
            self._update_info('job')

        return self.__convert_dttm_to_epoch('started')

    @property
    def status(self):
        """
        Get the job's status.

        Example:
            >>> running_job.status
            u'RUNNING'

        Returns:
            str: Job status.
        """

        if (self._status is None) or (self._status in RUNNING_STATUSES):
            self._status = self._adapter.get_status(self._job_id).upper()
            self._info['status'] = self._status

        return self._status.upper() if self._status else None

    def stderr(self, iterator=False):
        """
        Get the job's stderr as either an iterator or full text.

        Example:
            >>> running_job.stderr()
            '...'
            >>> for l in running_job.stderr(iterator=True):
            >>>     print(l)

        Args:
            iterator (bool, optional): Set to True if want to return as iterator.

        Returns:
            str or iterator.
        """

        if self.is_done:
            if not self._cached_stderr:
                self._cached_stderr = self._adapter.get_stderr(self._job_id)
            return self._cached_stderr.split('\n') if iterator \
                else self._cached_stderr
        return self._adapter.get_stderr(self._job_id, iterator=iterator)

    @property
    def stdout_url(self):
        """
        Returns a url for the stdout of the job.
        """

        return '{}/stdout'.format(self.output_uri.replace('/output/', '/file/', 1))

    def stdout(self, iterator=False):
        """
        Get the job's stdout as either an iterator or full text.

        Example:
            >>> running_job.stdout()
            '...'
            >>> for l in running_job.stdout(iterator=True):
            >>>     print(l)

        Args:
            iterator (bool, optional): Set to True if want to return as iterator.

        Returns:
            str or iterator.
        """

        return self._adapter.get_stdout(self._job_id, iterator=iterator)

    @property
    @get_from_info('status_msg', info_section='job', update_if_running=True)
    def status_msg(self):
        """
        Get the job's status message.

        Example:
            >>> running_job.status_msg
            u'Job is running'

        Returns:
            str: Job status message.
        """

    @property
    @get_from_info('tags', info_section='job')
    def tags(self):
        """
        Get the job's tags.

        Example:
            >>> running_job.tags
            [u'tag1', u'tag2']

        Returns:
            list: A list of tags.
        """

    def update(self, **kwargs):
        """Update all the job information."""

        self._update_info()

    @property
    def update_time(self):
        """
        Get the last update time for the job in epoch time (milliseconds).

        Example:
            >>> running_job.update_time
            1234567890

        Returns:
            int: The update time in epoch (milliseconds).
        """

        status = self.status

        if ('updated' not in self.info) \
                or status in RUNNING_STATUSES \
                or status is None:
            self._update_info('job')

        return self.__convert_dttm_to_epoch('updated')

    @property
    @get_from_info('user', info_section='job')
    def username(self):
        """
        Get the username the job was executed as.

        Example:
            >>> running_job.username
            u'jsmith'

        Returns:
            str: The username.
        """

    def wait(self, sleep_seconds=10, suppress_stream=False, until_running=False):
        """
        Blocking call that will wait for the job to complete.

        Example:
            >>> running_job.wait()

        Args:
            sleep_seconds (int, optional): The number of seconds to sleep while
                polling to get job status (default: 10).
            suppress_stream (bool, optional): If True, do not write anything to
                the sys stream (stderr/stdout) while waiting for the job to
                complete (default: False).
            until_running (bool, optional): If True, only block until the job
                status becomes 'RUNNING' (default: False).

        Returns:
            :py:class:`RunningJob`: self
        """

        i = 0

        statuses = {s for s in RUNNING_STATUSES \
            if not until_running or s.upper() != 'RUNNING'}

        while self._adapter.get_status(self._job_id).upper() in statuses:
            if i % 3 == 0 and not suppress_stream:
                self._write_to_stream('.')
            time.sleep(sleep_seconds)
            i += 1

        if not suppress_stream:
            self._write_to_stream('\n')

        return self

    def _write_to_stream(self, msg):
        """Writes message to the configured sys stream."""

        if self._sys_stream is not None:
            self._sys_stream.write(msg)
            self._sys_stream.flush()
