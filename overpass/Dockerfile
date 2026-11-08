FROM wiktorn/overpass-api:latest

USER root
RUN mv /usr/bin/curl /usr/bin/curl.real && \
    cat > /usr/local/bin/curl <<'EOF'
#!/bin/sh
OUT=""
WRITE_OUT=""
prev=""
for arg in "$@"; do
    case "$prev" in
        -o|--output)
            OUT="$arg"
            prev=""
            continue
            ;;
        -w|--write-out)
            WRITE_OUT="$arg"
            prev=""
            continue
            ;;
    esac
    case "$arg" in
        -o|--output) prev="-o" ;;
        -w|--write-out) prev="-w" ;;
        *) prev="" ;;
    esac
done

if [ -n "$OUT" ]; then
    cp /db/planet.osm.bz2.orig "$OUT"
fi

if [ -n "$WRITE_OUT" ]; then
    printf "200"
else
    if [ -z "$OUT" ]; then
        cat /db/planet.osm.bz2.orig
    fi
fi

exit 0
EOF
RUN chmod +x /usr/local/bin/curl
