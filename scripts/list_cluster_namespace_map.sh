clusters=( $(ls) )

for cluster in "${clusters[@]}"; do
    namespaces=$(grep -ir namespace $cluster | awk -Fnamespace: '{print $2}' | sort | uniq)
    echo "$cluster $namespaces"
done
