import os
import signal
import logging
import inspect
import json
from flask import Flask, request
from functools import wraps, update_wrapper
import types
from subprocess import Popen
from pymacaron.auth import get_user_token
from pymacaron.auth import load_auth_token
from pymacaron.crash import generate_crash_handler_decorator
from pymacaron.config import get_config
from pymacaron_async.serialization import model_to_task_arg
from pymacaron_async.serialization import task_arg_to_model
from pymacaron_async.app import app


log = logging.getLogger(__name__)


flaskapp = Flask('pym-async')


def get_celery_cmd(debug=False, keep_alive=False, concurrency=None):
    level = 'debug' if debug else 'info'
    maxmem = 200 * 1024
    if not concurrency:
        conf = get_config()
        if hasattr(conf, 'worker_count'):
            # Start worker_count parrallel celery workers
            concurrency = conf.worker_count
        else:
            # Default to 8 parrallel celery workers
            concurrency = 8

    cmd = 'pymasync --concurrency %s --maxmem %s --level %s' % (concurrency, maxmem, level)

    return cmd


def kill_celery():
    """Kill celery workers"""
    for line in os.popen("ps ax | grep 'celery worker -E -A pymacaron_async' | grep -v grep"):
        fields = line.split()
        pid = fields[0]
        log.warn("Killing celery worker with pid %s" % pid)
        os.kill(int(pid), signal.SIGTERM)


def start_celery(port, debug, concurrency=None):
    """Start celery workers"""

    # First, stop currently running celery workers (only those running pymacaron microservices)
    kill_celery()

    # Then start celery anew
    os.environ['PYM_CELERY_PORT'] = str(port)
    os.environ['PYM_CELERY_DEBUG'] = '1' if debug else ''
    cmd = get_celery_cmd(debug=debug, keep_alive=False, concurrency=concurrency)

    log.info("Spawning celery worker")
    Popen(
        [cmd],
        bufsize=0,
        shell=True,
        close_fds=True
    )


def copy_func(f):
    """Based on http://stackoverflow.com/a/6528148/190597 (Glenn Maynard)"""
    g = types.FunctionType(
        f.__code__,
        f.__globals__,
        name=f.__name__,
        argdefs=f.__defaults__,
        closure=f.__closure__,
    )
    g = update_wrapper(g, f)
    g.__kwdefaults__ = f.__kwdefaults__
    return g


class asynctask(object):

    def __init__(self, delay=0):
        self.delay = delay
        self.magic = 'PYMACARON_ALSO_WHIPS_THE_LLAMA_S'


    def exec_f(self, f, fname, args, kwargs):
        """Execute the method f asynchronously, in a mocked flask context"""
        url = args[1]
        token = args[2]
        args = args[3:]

        log.info('')
        log.info('')
        log.info(' => ASYNC TASK %s (delayed: %s sec)' % (fname, self.delay))
        log.info('')
        log.info('')
        log.info('    url: %s' % url)
        log.info('    token: %s' % token)
        log.debug('    args: %s' % json.dumps(args, indent=4))
        log.debug('    kwargs: %s' % json.dumps(kwargs, indent=4))
        log.info('')
        log.info('')

        # Restore PyMacaron Models passed in arguments as json
        args = [task_arg_to_model(o) for o in args]
        for k in list(kwargs.keys()):
            kwargs[k] = task_arg_to_model(kwargs[k])

        # Simulate a flask context, with the initial url and auth token
        with flaskapp.test_request_context(url):
            if token:
                load_auth_token(token)

            # Wrap f in the same crash handler as used in the API
            def do_f(*args2, **kwargs2):
                f(*args2, **kwargs2)
            cf = generate_crash_handler_decorator()(do_f)

            # And execute the actual asynchronous method
            cf(*args, **kwargs)


    def __call__(self, f, *aargs):

        # Catch silly syntax error, when using decorator with @asynctask, without ()
        if len(aargs):
            raise Exception("You forgot parenthesis when calling asynctask: @asynctask()")

        # Find out the decorated method's name
        fname = f.__name__
        m = inspect.getmodule(f)
        if m:
            fname = '%s.%s()' % (m.__name__, f.__name__)

        # And wrap the decorated method with the flask emulator
        @wraps(f)
        def exec_or_schedule_f(*args, **kwargs):

            arg0 = None
            if args and len(args) > 0:
                arg0 = args[0]

            # Is this code called with the magic word as first param?
            if arg0 == self.magic:
                # Weee!! We are running asynchronous. Let's mock the flask
                # context and execute the sync method

                self.exec_f(f, fname, args, kwargs)

            else:
                # No magic keyword: this is a direct call. Let's wrap the called
                # method in a celery task and schedule it

                # Serialize PyMacaron Models into simple python primitives supported by celery
                args = tuple([model_to_task_arg(o) for o in args])
                for k in list(kwargs.keys()):
                    kwargs[k] = model_to_task_arg(kwargs[k])

                # And queue up the task
                log.info('Queuing celery task for %s with delay=%s' % (fname, self.delay))

                ff = copy_func(f)
                ff = app.task(ff, typing=False)

                url = request.url
                token = get_user_token()
                args = (self.magic, url, token) + args
                ff.apply_async(args=args, kwargs=kwargs, countdown=self.delay)

        # Return the wrapped task
        log.info("Registering celery task for %s (delay: %s)" % (fname, self.delay))
        newf = app.task(exec_or_schedule_f, typing=False)
        return newf
