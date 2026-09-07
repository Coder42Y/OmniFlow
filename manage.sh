#!/usr/bin/env bash
# AIPannel 运维管理脚本

ACTION=$1

get_tunnel_url() {
    if [ -f "/home/kris/Codes/AIPannel/tunnel.log" ]; then
        grep -o 'https://[-a-zA-Z0-9]*\.trycloudflare\.com' /home/kris/Codes/AIPannel/tunnel.log | tail -n 1
    fi
}

case "$ACTION" in
    start)
        echo "正在启动 AIPannel 服务端与公网隧道..."
        systemctl --user daemon-reload
        systemctl --user enable aipannel.service aipannel-tunnel.service
        systemctl --user start aipannel.service aipannel-tunnel.service
        sleep 2
        $0 status
        ;;
    stop)
        echo "正在停止 AIPannel 服务端与公网隧道..."
        systemctl --user stop aipannel.service aipannel-tunnel.service
        $0 status
        ;;
    restart)
        echo "正在重启 AIPannel 服务端与公网隧道..."
        systemctl --user daemon-reload
        systemctl --user restart aipannel.service aipannel-tunnel.service
        sleep 2
        $0 status
        ;;
    status)
        echo "=================== AIPannel 运行状态 ==================="
        systemctl --user status aipannel.service --no-pager -l | head -n 10
        echo "---------------------------------------------------------"
        systemctl --user status aipannel-tunnel.service --no-pager -l | head -n 10
        echo "========================================================="
        LOCAL_IP=$(hostname -I | awk '{print $1}')
        TUNNEL_URL=$(get_tunnel_url)
        echo "内网局域网访问地址: http://${LOCAL_IP}:8080"
        echo "本机回路访问地址:   http://127.0.0.1:8080"
        echo "公网 HTTPS 直连地址: ${TUNNEL_URL:-'隧道获取中或未启动'}"
        echo "========================================================="
        ;;
    logs)
        journalctl --user -u aipannel.service -f
        ;;
    url)
        get_tunnel_url
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status|logs|url}"
        exit 1
        ;;
esac
