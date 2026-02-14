ARG BASE_IMG=alpine:3.19

FROM $BASE_IMG AS pidproxy

RUN apk --no-cache add alpine-sdk \
 && git clone https://github.com/ZentriaMC/pidproxy.git \
 && cd pidproxy \
 && git checkout 193e5080e3e9b733a59e25d8f7ec84aee374b9bb \
 && sed -i 's/-mtune=generic/-mtune=native/g' Makefile \
 && make \
 && mv pidproxy /usr/bin/pidproxy \
 && cd .. \
 && rm -rf pidproxy \
 && apk del alpine-sdk

FROM $BASE_IMG

COPY --from=pidproxy /usr/bin/pidproxy /usr/bin/pidproxy
RUN apk --no-cache add vsftpd tini tzdata bash python3 py3-pip shadow
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir fastapi uvicorn

ENV TZ=America/Sao_Paulo
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY start_vsftpd.sh /bin/start_vsftpd.sh
COPY vsftpd.conf /etc/vsftpd/vsftpd.conf
COPY manager_api.py /opt/ftp-manager/manager_api.py

RUN chmod +x /bin/start_vsftpd.sh

RUN mkdir -p /ftp /data && \
    chmod -R 775 /ftp && \
    chown -R root:root /ftp

EXPOSE 21 50000-50100 8080
VOLUME /ftp /data

ENTRYPOINT ["/sbin/tini", "--", "/bin/start_vsftpd.sh"]