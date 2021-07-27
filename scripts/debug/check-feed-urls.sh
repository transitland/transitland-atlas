cat $1 | jq '.feeds[] .urls.static_current' -r | xargs -I URL curl -sL -I -w "%{http_code}\
tURL\\n" "URL" -o /dev/null