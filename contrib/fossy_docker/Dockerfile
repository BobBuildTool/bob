FROM fossology/fossology

RUN apt-get update && apt-get install -y openssh-server
RUN mkdir /var/run/sshd
RUN sed -i 's/PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

# SSH login fix. Otherwise user is kicked off after login
RUN sed 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' -i /etc/pam.d/sshd

ENV NOTVISIBLE "in users profile"
RUN echo "export VISIBLE=now" >> /etc/profile

RUN sed -i 's@# Start Fossology@# Start Fossology\n\/usr\/sbin\/sshd@' /fossology/docker-entrypoint.sh

RUN adduser --quiet bob
RUN groupadd fossy
RUN useradd -c FOSSology -d /home/fossy -g fossy -s /bin/bash fossy
RUN usermod -a -G fossy bob
RUN echo 'bob:bob' | chpasswd

EXPOSE 22

