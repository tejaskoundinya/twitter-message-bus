#! /usr/bin/env python2.7

'''
Push tweets to Twitter; if the tweet has a TTL, push it to the 'out' queue.
'''


import re
import json
from datetime import datetime
from argparse import ArgumentParser
from logging import (NullHandler, getLogger, StreamHandler, Formatter, DEBUG,
                     INFO)

import tweepy
import magic
from pydisque.client import Client

from gist import post
from auth import status, lookup, encrypt, sign

# Formatting for logger output.
getLogger(__name__).addHandler(NullHandler())
LOGGER = getLogger()
HANDLER = StreamHandler()
FORMATTER = Formatter(
    ('%(asctime)s; %(name)s, %(levelname)s; PID: %(process)s; '
     '%(module)s: %(funcName)s; traceback: %(exc_info)s; %(message)s')
)
HANDLER.setFormatter(FORMATTER)


# Check stream.py for more information.
VAULT_PATH = 'vault/keys.json'


def load_credentials(path=VAULT_PATH):
    '''
    Load credentials from vault.
    '''
    gist, api = None, None
    with open(path, 'r') as vault_file:
        try:
            vault = json.loads(vault_file.read())
            auth = tweepy.OAuthHandler(vault['twitter']['consumer-key'],
                                       vault['twitter']['consumer-secret'])
            auth.set_access_token(vault['twitter']['access-token'],
                                  vault['twitter']['access-token-secret'])
            api = tweepy.API(auth)
            gist = vault['github']

        except IOError:
            print 'Unable to read vault-file: {0}.'.format(path)
        except (KeyError, ValueError):
            print 'Unable to parse the vault-file.'

    return gist, api


def send(plaintext, auth, recipient, ttl=0, **kwargs):
    '''
    Encrypt the contents to a keybase-saltpack; push it to Twitter, GitHub.
    '''
    queue = kwargs['queue'] if 'queue' in kwargs else None
    debug = kwargs['debug'] if 'debug' in kwargs else False
    future = int(datetime.utcnow().strftime('%s')) + ttl
    prefix = 'twitter-message-bus'

    if status(debug):
        LOGGER.info('[keybase-status] client-up; signed-in')

        # Do a look-up on Keybase for a valid recipient ID.
        if lookup(recipient, debug):
            LOGGER.info('[keybase-lookup] %s exists', recipient)
            # Encrypt the document.
            encrypted = encrypt(plaintext, recipient, debug)
            # Sign the document.
            signed = sign(encrypted, debug)
            # Post the gist.
            gist_id, _hash = post(content=signed, username=recipient,
                                  debug=debug, token=auth[0])
            if gist_id:
                prefix = '-'.join([prefix, _hash])
                LOGGER.info('[gist] %s', gist_id)

            try:
                # Logic for gists/tweets with TTL.
                if gist_id and ttl and queue and encrypted:
                    message = '~'.join(['gist', gist_id, str(future)])
                    queue.add_job('out', message)
                    LOGGER.info('[gist-queue] added %s to \'out\'', message)

                tweet = None
                if gist_id:
                    tweet = auth[1].update_status(':'.join([prefix, gist_id]))
                    LOGGER.debug('[tweet] %s', tweet)
                    LOGGER.info('[tweet] %s', tweet.id)

                if tweet and ttl and queue:
                    message = '~'.join(['tweet', tweet.id_str, str(future)])
                    queue.add_job('out', message)
                    LOGGER.info('[tweet-queue] added %s to \'out\'', message)

                return gist_id, tweet.id
            except Exception:
                LOGGER.error('[queue] unable to write to queue; data lost!')

        else:
            LOGGER.error('[keybase-lookup] lookup for %s failed!', recipient)

    else:
        LOGGER.error('[keybase-status] client-down/signed-out!')


def main():
    '''
    Validate arguments; send data to the message bus.
    '''
    message = 'Push data to the message bus.'
    socket_help = ('a list containing the host, port numbers to listen to; '
                   'defaults to localhost:7711 (for disque)')
    ttl_help = ('a TTL (in seconds) for the data on Twitter and GitHub; '
                'if not specified, the data will remain forever')

    parser = ArgumentParser(description=message)
    parser.add_argument('-s', '--sockets', help=socket_help,
                        default=['localhost:7711'], dest='sockets',
                        metavar=('HOST:PORT'), nargs='+')
    parser.add_argument('-d', '--debug', help='enable debugging',
                        action='store_true', default=False)
    parser.add_argument('-r', '--recipient', help='keybase-id to send',
                        required=True, metavar=('KEYBASE-ID'))
    parser.add_argument('-t', '--ttl', help=ttl_help, default=0,
                        type=int, metavar=('N'))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-i', '--in-file', metavar=('FILE'),
                       default=None)
    group.add_argument('-m', '--message', type=str)

    args = vars(parser.parse_args())

    if args['debug']:
        LOGGER.setLevel(DEBUG)
        LOGGER.addHandler(HANDLER)
    else:
        LOGGER.setLevel(INFO)
        LOGGER.addHandler(HANDLER)

    plaintext, queue = None, None

    if args['in_file']:
        name = args['in_file']
        if not re.match(r'^text\/.*', magic.from_file(name, mime=True)):
            LOGGER.error('[file-error] input-file mimetype should be text/.*')
            return
        else:
            plaintext = open(name, 'r').read()
    else:
        plaintext = args['message']

    try:
        # Instantiate a connection to the queue only if a TTL is specified.
        if args['ttl']:
            queue = Client(args['sockets'])
            queue.connect()
            queue_info = json.dumps(queue.info(), indent=4)
            LOGGER.debug('[queue-init]\n%s', queue_info)

        auth = load_credentials()
        if None in auth:
            LOGGER.error('[load_credentials] unable to load credentials!')
            return

        send(plaintext=plaintext, auth=auth, recipient=args['recipient'],
             ttl=args['ttl'], queue=queue, debug=args['debug'])

    except Exception:
        LOGGER.error('[error] unable to connect to the redis-queue (disque)!')


if __name__ == '__main__':
    main()
