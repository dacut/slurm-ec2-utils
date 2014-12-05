#!/usr/bin/python
from __future__ import absolute_import, print_function
import boto.sqs
from boto.sqs.message import Message
from .clusterconfig import ClusterConfiguration
from getopt import getopt, GetoptError
from .instanceinfo import get_region
from json import dumps as json_dumps, loads as json_loads
from os import environ, urandom
from signal import signal, SIGUSR1
from subprocess import PIPE, Popen
from sys import argv, stderr, stdout
from time import sleep
from types import NoneType

BUFSIZE = 1 << 20 # 1 MB
SLEEP_TIME = 5
MAX_TIMES_EMPTY = 3

exit_requested = False

def request_exit():
    global exit_requested
    exit_requested = True

def get_sqs():
    cc = ClusterConfiguration.from_config()
    region = get_region()
    return boto.sqs.connect_to_region(region)

def run_tasks():
    global exit_requested
    queue_id = environ.get("SLURM_EC2_QUEUE_ID")
    if queue_id is None:
        print("SLURM_EC2_QUEUE_ID environment variable not set", file=stderr)
        return 1

    request_queue_name = "slurm-%s-request" % queue_id
    response_queue_name = "slurm-%s-response" % queue_id

    sqs = get_sqs()
    request_queue = sqs.get_queue(request_queue_name)
    response_queue = sqs.get_queue(response_queue_name)

    # Handle a USR1 signal by setting the exit_requested flag -- we'll exit
    # when we find no more tasks in the queue.
    signal(SIGUSR1, request_exit)

    # Keep reading tasks from the request queue.
    while True:
        msg = request_queue.read()
        if msg is None:
            if exit_requested:
                break

            # Don't exit just yet...
            sleep(SLEEP_TIME)
            continue

        print("Message received: %r" % msg.get_body())
        
        try:
            # Decode the message as JSON
            request = json_loads(msg.get_body())

            print("Message decoded: %r" % (request,))

            id = request.get("id")
            if id is None:
                raise ValueError("Missing id in message")

            cmd = request.get("cmd")
            env = request.get("env")

            if cmd is None:
                # No command to execute.
                exit_code = 127
                err = "No command to execute"
                print(err)
                out = ""
            elif not isinstance(cmd, (list, tuple)):
                # Invalid command line
                exit_code = 127
                err = ("Invalid command -- expected list instead of %s" %
                       (type(cmd).__name__))
                print(err)
                out = ""
            elif not isinstance(env, (dict, NoneType)):
                # Invalid environment
                exit_code = 127
                err = ("Invalid environment -- expected dict instead of %s" %
                       (type(cmd).__name__))
                print(err)
                out = ""
            else:
                print("Invoking: %r" % (cmd,))
                print("Environment: %r" % (env,))

                proc = Popen(cmd, bufsize=BUFSIZE, stdin=PIPE, stdout=PIPE,
                             stderr=PIPE, close_fds=True, shell=False,
                             env=env)

                out, err = proc.communicate()
                exit_code = proc.returncode

                print("Process exited with exit_code %d" % exit_code)
                print("stdout:-----")
                print(out)
                print("stderr:-----")
                print(err)

                del proc

            response = response_queue.new_message(json_dumps({
                'id': id,
                'exit_code': exit_code,
                'stdout': out,
                'stderr': err
            }))
            response_queue.write(response)
            msg.delete()
        except ValueError as e:
            # Yikes.  Log this error and give up processing the message
            # (silently fail).
            print("Unable to decode message: %r" % (msg.get_body(),))
            msg.delete()
            
    return 0

def initialize_queue():
    queue_id = "".join(["%02x" % ord(x) for x in urandom(10)])
    request_queue_name = "slurm-%s-request" % queue_id
    response_queue_name = "slurm-%s-response" % queue_id
    timeout = 43200

    def usage():
        stderr.write("""\
Usage: %s [--timeout=<timeout in seconds>]
Timeout must be an integer from 0 to 43200 (12 hours).
""" % (argv[0],))
        return

    try:
        opts, args = getopt(argv[1:], "t:", ["timeout="])
    except GetoptError:
        usage()
        return 1

    if len(args) > 0:
        print("Unknown argument %s" % args[0], file=stderr)
        usage()
        return 1

    for opt, value in opts:
        if opt in ("-t", "--timeout"):
            try:
                timeout = int(value)
                if not (0 <= timeout <= 43200):
                    raise ValueError()
            except ValueError:
                print("Invalid timeout value %r" % value, file=stderr)
                usage()
                return 1

    sqs = get_sqs()

    request_queue = sqs.create_queue(request_queue_name, timeout)
    response_queue = sqs.create_queue(response_queue_name, timeout)

    try:
        request_queue.set_attribute("ReceiveMessageWaitTimeSeconds", 20)
        response_queue.set_attribute("ReceiveMessageWaitTimeSeconds", 20)
    except Exception as e:
        # Ignore if unsupported
        pass

    print("export SLURM_EC2_QUEUE_ID=%s" % queue_id)
    return 0

def submit_task():
    queue_id = environ.get("SLURM_EC2_QUEUE_ID")
    task_id = "task-%s" % "".join(["%02x" % ord(x) for x in urandom(10)])

    if queue_id is None:
        print("SLURM_EC2_QUEUE_ID environment variable not set", file=stderr)
        return 1

    request_queue_name = "slurm-%s-request" % queue_id
    sqs = get_sqs()
    request_queue = sqs.get_queue(request_queue_name)
    request = request_queue.new_message(json_dumps({
        "id": task_id,
        "cmd": argv[1:],
        "env": dict(environ),
    }))
    request_queue.write(request)
    print(task_id)
    return 0

def wait_tasks():
    queue_id = environ.get("SLURM_EC2_QUEUE_ID")
    if queue_id is None:
        print("SLURM_EC2_QUEUE_ID environment variable not set", file=stderr)
        return 1
    
    request_queue_name = "slurm-%s-request" % queue_id
    response_queue_name = "slurm-%s-response" % queue_id
    sqs = get_sqs()
    request_queue = sqs.get_queue(request_queue_name)
    response_queue = sqs.get_queue(response_queue_name)

    times_empty = 0
    
    while True:
        msg = response_queue.read()
        if msg is None:
            # Are there pending requests?
            attrs = request_queue.get_attributes()
            in_flight = (int(attrs['ApproximateNumberOfMessages']) +
                         int(attrs['ApproximateNumberOfMessagesNotVisible']))

            if in_flight == 0:
                times_empty += 1
            else:
                times_empty = 0

            # If we've not seen any responses and haven't found any unserved
            # requests for MAX_TIMES_EMPTY polls, stop.
            if times_empty >= MAX_TIMES_EMPTY:
                break
                
            if in_flight == 0:
                attrs = response_queue.get_attributes()
                long_poll_time = int(
                    attrs.get('ReceiveMessageWaitTimeSeconds', 0))

                print("No tasks in flight... will wait %d more second(s)" %
                      ((MAX_TIMES_EMPTY - times_empty) *
                       (SLEEP_TIME + long_poll_time)))
            else:
                print("%s task(s) in flight, but none are ready" %
                      (in_flight,))

            sleep(SLEEP_TIME)
            continue
        
        times_empty = 0
        
        try:
            # Decode the message as JSON
            response = json_loads(msg.get_body())
            id = response.get("id")
            exit_code = response.get("exit_code")

            log_dirs = [".", environ["HOME"], "/tmp", "/var/tmp"]

            for log_dir in log_dirs:
                filename = "%s/%s.json" % (log_dir, id)
                try:
                    fd = open(filename, "w")
                    break
                except IOError:
                    pass
            else:
                # Couldn't open a log file.
                filename = "/dev/null"
                fd = open(filename, "w")

            fd.write(msg.get_body())
            fd.close()
            
            print("Task %s finished with exit code %s; logged to %s" %
                  (id, exit_code, filename))
        except:
            pass
        
        msg.delete()

    sqs.delete_queue(request_queue)
    sqs.delete_queue(response_queue)
    return 0
