def monthly_report(events, year, month):
    in_month = [e for e in events if e["date"].year == year and e["date"].month == month]
    checkouts = [e for e in in_month if e["kind"] == "checkout"]
    returns = [e for e in in_month if e["kind"] == "return"]
    counts = {}
    for e in checkouts:
        counts[e["book_id"]] = counts.get(e["book_id"], 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return {"checkouts": len(checkouts), "returns": len(returns),
            "fines_cents": sum(e["fine_cents"] for e in returns), "top_books": top}
