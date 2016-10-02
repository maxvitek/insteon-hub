import time
import json
import requests
import threading

# http://cache.insteon.com/developer/2242-222dev-062013-en.pdf


class BufferParsingError(Exception):
    pass


class BufferExhausted(Exception):
    pass


class SingleGetter(object):
    def __init__(self):
        self._hub_lock = threading.Lock()

    def get(self, *args, **kwargs):
        with self._hub_lock:
            time.sleep(1)
            resp = requests.get(*args, **kwargs)
            resp.raise_for_status()
            return resp


class LocalHub(object):
    '''
    Abstraction for managing interaction locally with the Insteon 
    modem via the hub.
    '''
    def __init__(self, user, password, host, port, status_ttl=60):
        self.status_ttl = status_ttl
        self.auth = (user, password)
        self.url = 'http://{}:{}'.format(host, port)
        self.single_getter = SingleGetter()

    def poll(self):
        data = self.single_getter.get(self.url + '/buffstatus.xml',
                            auth=self.auth).text
        # parse html tag
        data = data.split('<BS>')[1].split('</BS>')[0]
        # trim end chars
        data = data[0:-2]
        return self.parse(data)

    def clear(self):
        r = self.single_getter.get(self.url + '/1?XB=M=1',
                         auth=self.auth)
        return True

    def _command(self, device_id, cmd1, cmd2='00', flags='0F'):
        _pass_through = '0262'
        msg = (_pass_through + device_id + flags + cmd1 + cmd2).lower()
        r = self.single_getter.get(self.url + '/3?{}=I=3'.format(msg), auth=self.auth)
        r.raise_for_status()
        return True

    def device_on(self, device_id, level=255):
        cmd2 = hex(level).replace('0x', '').upper()
        return self._command(device_id, '11', cmd2)

    def device_off(self, device_id):
        return self._command(device_id, '13')

    def device_status(self, device_id):
        return self._command(device_id, '19')

    def parse(self, buff):
        buffer_contents = []
        while buff:
            try:
                ack, buff = self._parse_ack(buff)
            except BufferParsingError:
                # skip to next message
                old_buff = buff
                _, delim, buff = buff.partition('0262')
                buff = delim + buff
                if old_buff == buff:
                    break
                else:
                    continue
            try:
                while True:
                    msg, buff = self._parse_msg(buff)
                    ack['response'].append(msg)
            except BufferParsingError:
                buffer_contents.append(ack)
                pass
            except BufferExhausted:
                pass
        return buffer_contents

    def _parse_ack(self, buff):
        _pass_through = buff[:4]
        _id = buff[4:10]
        _flags = buff[10:12]
        _cmd1 = buff[12:14]
        _cmd2 = buff[14:16]
        _ack = buff[16:18]
        try:
            assert _pass_through == '0262'
            assert _ack == '06'
        except AssertionError:
            raise BufferParsingError('unparsable buffer: {}'.format(buff))
        buff = buff[18:]
        if _cmd1 == '19':
            command = 'status_request'
        elif _cmd1 == '11':
            command = 'on'
        elif _cmd1 == '13':
            command = 'off'
        # more here
        else:
            command = 'unknown[{}::{}]'.format(_cmd1, _cmd2)
        ack = {
            'device': _id,
            'flags': _flags,
            'command': command,
            'response': []
        }
        return ack, buff

    def _parse_msg(self, buff):
        try:
            buff[22]
        except IndexError:
            raise BufferExhausted()
        _msg_flag = buff[:4]
        _from = buff[4:10]
        _to = buff[10:16]
        _flags = buff[16:18]
        _cmd1 = buff[18:20]
        _cmd2 = buff[20:22]
        try:
            assert _msg_flag == '0250'
        except AssertionError:
            raise BufferParsingError('unparsable buffer: {}'.format(buff))
        buff = buff[20:]
        if _cmd1 in ['11', '13', '19']:
            status = int(_cmd2, 16)
        else:
            status = 'unknown[{}::{}]'.format(_cmd1, _cmd2)
        if _cmd1 == '19':
            _type = 'status'
        elif _cmd1 == '11':
            _type = 'on'
        elif _cmd1 == '13':
            _type = 'off'
        else:
            _type = _cmd1
        msg = {
            'from': _from,
            'to': _to,
            'flags': _flags,
            'status': status,
            'type': _type
        }
        return msg, buff

    def subscribe(self, callback):
        recent_msgs = {}
        while True:
            msgs = self.poll()
            for msg in msgs:
                hsh = hash(json.dumps(msg))
                now = time.time()
                if hsh not in recent_msgs or now - recent_msgs[hsh] > self.status_ttl:
                    # got something news-worthy, publish it!
                    callback(msg)
                recent_msgs[hsh] = now
